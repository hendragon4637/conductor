"""Shared LLM utility for meta-planner stages.

All three stages (goal-formulator, decomposer, check-generator) use the same
model role (``meta_planner``) from ``config/brain_models.json``, and the same
``call_llm_structured`` helper that parses JSON into a Pydantic model.

Config-driven so the model is swappable without code changes.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request
from typing import TypeVar

from pydantic import BaseModel

from backend.planning.model_selector import get_brain_model

logger = logging.getLogger(__name__)

# Max retries for truncated responses
_MAX_RETRIES = 1

T = TypeVar("T", bound=BaseModel)


def get_meta_planner_model() -> dict:
    """Resolve the meta_planner model config (primary → fallback).

    Returns:
        dict with ``provider``, ``model``, ``base_url`` keys.
    """
    return dict(get_brain_model("meta_planner"))


def call_llm_structured(
    prompt: str,
    schema: type[T],
    model_cfg: dict | None = None,
    temperature: float = 0.1,
    max_tokens: int = 8192,
) -> T:
    """Call the meta-planner LLM and parse the response as a Pydantic model.

    Args:
        prompt: The full prompt (system + user instructions).
        schema: The Pydantic model class to parse the response into.
        model_cfg: Optional model config (from ``get_meta_planner_model()``).
            If omitted, resolves the model dynamically.
        temperature: LLM temperature (default 0.1 for deterministic output).
        max_tokens: Maximum output tokens.

    Returns:
        An instance of ``schema`` parsed from the LLM response.

    Raises:
        RuntimeError: If the LLM response cannot be parsed after retries.
    """
    if model_cfg is None:
        model_cfg = get_meta_planner_model()

    base_url = model_cfg["base_url"].rstrip("/")
    if not base_url.endswith("/chat/completions"):
        base_url += "/chat/completions"

    schema_desc = _schema_description(schema)

    system_msg = (
        "You are a structured plan-composition engine. "
        "You output ONLY valid JSON matching the requested schema. "
        "Never include explanations, markdown fences, or extra text. "
        f"The expected JSON schema is: {schema_desc}"
    )

    body = {
        "model": model_cfg["model"],
        "messages": [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    headers = {
        "Content-Type": "application/json",
        "User-Agent": "conductor-meta-planner/1.0",
    }
    api_key_env = model_cfg.get("api_key_env")
    if api_key_env:
        api_key = os.environ.get(api_key_env, "")
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

    last_error: str | None = None

    for attempt in range(_MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(
                base_url,
                data=json.dumps(body).encode(),
                headers=headers,
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
                result = json.loads(resp.read())
            raw = result["choices"][0]["message"]["content"]

            # Strip markdown fences if present
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
                # Append error context and retry
                body["messages"].append({
                    "role": "user",
                    "content": (
                        f"Your previous response failed validation: {last_error}. "
                        "Return ONLY valid JSON matching the schema. "
                        "No markdown, no explanation."
                    ),
                })
                body["max_tokens"] = max_tokens * 2

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
