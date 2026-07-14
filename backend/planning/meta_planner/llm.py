"""Shared LLM utility for meta-planner stages.

All three stages (goal-formulator, decomposer, check-generator) use the same
model role (``meta_planner``) from the LiteLLM gateway, and the same
``call_llm_structured`` helper that parses JSON into a Pydantic model.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import TypeVar

from pydantic import BaseModel

from backend.llm.gateway import call as gateway_call

logger = logging.getLogger(__name__)

# Max retries for failed LLM calls
_MAX_RETRIES = 5
# Base backoff seconds (doubles each retry)
_BACKOFF_BASE = 5.0
# Max backoff ceiling
_BACKOFF_MAX = 120.0
# Minimum delay for 429 rate-limit responses
_BACKOFF_429_MIN = 30.0
# Minimum gap between consecutive LLM calls (spreads TPM consumption)
_COOLDOWN_BETWEEN_CALLS = 15.0
# Per-request HTTP timeout (deepseek-planning can be slow for complex prompts)
_GATEWAY_TIMEOUT = 300.0

# Track last call end time for inter-call cooldown
_last_call_end: float = 0.0

T = TypeVar("T", bound=BaseModel)


def get_meta_planner_model() -> dict:
    """Return a marker dict for backward-compatible callers."""
    return {"provider": "litellm", "model": "deepseek-planning", "base_url": "http://litellm:4000/v1"}


def call_llm_structured(
    prompt: str,
    schema: type[T] | None = None,
    model_cfg: dict | None = None,
    temperature: float = 0.1,
    max_tokens: int = 8192,
    role: str = "meta_planner",
    include_raw: bool = False,
) -> T | dict | list:
    """Call the meta-planner LLM through the LiteLLM gateway and parse the response.

    Args:
        prompt: The full prompt (system + user instructions).
        schema: The Pydantic model class to parse the response into.
        model_cfg: Ignored (kept for backward compat). Always uses gateway.
        temperature: LLM temperature (default 0.1 for deterministic output).
        max_tokens: Maximum output tokens.
        role: Gateway role for model selection (default "meta_planner").
        include_raw: If True, return ``(parsed, raw_text)`` tuple instead of
            parsed object alone.  Existing callers pass False (default) so
            this is fully backward compatible.

    Returns:
        When ``include_raw=False`` (default): an instance of ``schema`` parsed
        from the LLM response.
        When ``include_raw=True``: ``(parsed_or_dict, raw_text_or_None)`` tuple.
        ``raw_text`` is None only if all retries failed.

    Raises:
        RuntimeError: If the LLM response cannot be parsed after retries.
    """
    has_schema = schema is not None
    schema_desc = _schema_description(schema) if has_schema else "free-form JSON"

    system_msg = (
        "You are a structured plan-composition engine. "
        "You output ONLY valid JSON. "
        "Never include explanations, markdown fences, or extra text. "
        f"{'The expected JSON schema is:' if has_schema else 'Output free-form JSON according to the instructions.'} {schema_desc}"
    )

    last_error: str | None = None
    last_raw: str | None = None

    # Enforce minimum gap between consecutive LLM calls
    global _last_call_end
    since_last = time.time() - _last_call_end
    if since_last < _COOLDOWN_BETWEEN_CALLS and _last_call_end > 0:
        wait = _COOLDOWN_BETWEEN_CALLS - since_last
        logger.info("Cooldown before next LLM call: sleeping %.1fs", wait)
        time.sleep(wait)

    try:
        for attempt in range(_MAX_RETRIES + 1):
            try:
                result = gateway_call(role, [
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": prompt},
                ], temperature=temperature, max_tokens=max_tokens, timeout=_GATEWAY_TIMEOUT)
                raw = result["choices"][0]["message"]["content"]
                last_raw = raw

                text = raw.strip()
                if text.startswith("```"):
                    text = text.split("\n", 1)[1] if "\n" in text else text[3:]
                    text = text.rsplit("```", 1)[0] if "```" in text else text
                    text = text.strip()

                try:
                    parsed = schema.model_validate_json(text) if has_schema else json.loads(text)
                    if include_raw:
                        return (parsed, last_raw)
                    return parsed
                except Exception as parse_err:
                    logger.warning(
                        "meta_planner LLM call attempt %d — %s failed:\n"
                        "  error=%s\n"
                        "  raw_len=%d  raw_text=%s",
                        attempt + 1,
                        "Pydantic validation" if has_schema else "JSON parse",
                        parse_err,
                        len(text), text,
                    )
                    raise

            except Exception as exc:
                last_error = str(exc)
                logger.warning(
                    "meta_planner LLM call attempt %d failed: %s",
                    attempt + 1, last_error,
                )
                if attempt < _MAX_RETRIES:
                    err_str = str(exc)
                    is_429 = "429" in err_str or "Too Many Requests" in err_str
                    delay = min(_BACKOFF_BASE * (2 ** attempt), _BACKOFF_MAX)
                    if is_429:
                        # Respect server's retry-after header when available
                        m = re.search(r"retry-after\s*=\s*(\d+)", err_str)
                        ra = float(m.group(1)) if m else 0
                        delay = max(delay, ra, _BACKOFF_429_MIN)
                    logger.info(
                        "Retrying in %.1fs after attempt %d (429=%s)",
                        delay, attempt + 1, is_429,
                    )
                    time.sleep(delay)

        raise RuntimeError(
            f"meta_planner LLM failed after {_MAX_RETRIES + 1} attempts: {last_error}"
        )
    finally:
        _last_call_end = time.time()


def _schema_description(schema: type[BaseModel]) -> str:
    """Build a compact JSON schema description for the prompt.

    Includes nested model fields so the LLM knows the full structure.
    """
    try:
        full = schema.model_json_schema()
        return "\n" + json.dumps(full, indent=2)
    except Exception:
        # Fallback: flat listing
        fields = []
        for name, field in schema.model_fields.items():
            ann = str(field.annotation) if field.annotation else "unknown"
            req = "required" if field.is_required() else f"default={field.default!r}"
            desc = (field.description or "")[:80]
            fields.append(f"  {name} ({ann}, {req}): {desc}")
        return "\n" + "\n".join(fields)
