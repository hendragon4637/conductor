"""Shared LLM utility for meta-planner stages.

All three stages (goal-formulator, decomposer, check-generator) use the same
model role (``meta_planner``) from the LiteLLM gateway, and the same
``call_llm_structured`` helper that parses JSON into a Pydantic model.
"""

from __future__ import annotations

import json
import logging
from typing import TypeVar

from pydantic import BaseModel

from backend.llm.gateway import call as gateway_call

logger = logging.getLogger(__name__)

# Max retries for truncated responses
_MAX_RETRIES = 1

T = TypeVar("T", bound=BaseModel)


def get_meta_planner_model() -> dict:
    """Return a marker dict for backward-compatible callers."""
    return {"provider": "litellm", "model": "deepseek-planning", "base_url": "http://litellm:4000/v1"}


def call_llm_structured(
    prompt: str,
    schema: type[T],
    model_cfg: dict | None = None,
    temperature: float = 0.1,
    max_tokens: int = 8192,
) -> T:
    """Call the meta-planner LLM through the LiteLLM gateway and parse the response.

    Args:
        prompt: The full prompt (system + user instructions).
        schema: The Pydantic model class to parse the response into.
        model_cfg: Ignored (kept for backward compat). Always uses gateway.
        temperature: LLM temperature (default 0.1 for deterministic output).
        max_tokens: Maximum output tokens.

    Returns:
        An instance of ``schema`` parsed from the LLM response.

    Raises:
        RuntimeError: If the LLM response cannot be parsed after retries.
    """
    schema_desc = _schema_description(schema)

    system_msg = (
        "You are a structured plan-composition engine. "
        "You output ONLY valid JSON matching the requested schema. "
        "Never include explanations, markdown fences, or extra text. "
        f"The expected JSON schema is: {schema_desc}"
    )

    last_error: str | None = None

    for attempt in range(_MAX_RETRIES + 1):
        try:
            result = gateway_call("meta_planner", [
                {"role": "system", "content": system_msg},
                {"role": "user", "content": prompt},
            ], temperature=temperature, max_tokens=max_tokens)
            raw = result["choices"][0]["message"]["content"]

            text = raw.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1] if "\n" in text else text[3:]
                text = text.rsplit("```", 1)[0] if "```" in text else text
                text = text.strip()

            try:
                return schema.model_validate_json(text)
            except Exception as parse_err:
                logger.warning(
                    "meta_planner LLM call attempt %d — Pydantic validation failed:\n"
                    "  error=%s\n"
                    "  raw_len=%d  raw_text=%s",
                    attempt + 1, parse_err,
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
                pass

    raise RuntimeError(
        f"meta_planner LLM failed after {_MAX_RETRIES + 1} attempts: {last_error}"
    )


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
