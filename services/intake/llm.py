"""Thin OpenAI-compatible LLM helper for intake clarification answers.

Calls the LiteLLM gateway (same pattern as planner's LangGraph backend).
Only used by adapters that have structured data to synthesise into answers.
"""

from __future__ import annotations

import json
import logging
import os

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "gpt-4o-mini"  # fast & cheap for short clarification answers


def call_llm(system: str, user: str, max_tokens: int = 512) -> str:
    """Single-turn chat completion through the LiteLLM gateway.

    Args:
        system: System prompt.
        user: User message.
        max_tokens: Max output tokens.

    Returns:
        The assistant's reply text, or an empty string on failure.
    """
    base = (os.environ.get("LITELLM_BASE") or "http://litellm:4000/v1").rstrip("/")
    key = (os.environ.get("LITELLM_KEY_PLANNING")
           or os.environ.get("LITELLM_MASTER_KEY")
           or "")
    model = os.environ.get("INTAKE_LLM_MODEL", _DEFAULT_MODEL)

    try:
        resp = httpx.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "max_tokens": max_tokens,
                "temperature": 0,
            },
            timeout=30.0,
        )
        resp.raise_for_status()
        body = resp.json()
        return body["choices"][0]["message"]["content"] or ""
    except Exception:
        logger.exception("LLM call failed")
        return ""
