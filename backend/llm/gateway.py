"""Centralised LLM client — routes all Conductor LLM calls through the LiteLLM gateway.

Every Conductor component (meta-planner, plan-evaluator, L2 judge, execution
backends) should import ``gateway.call(role, messages, ...)`` instead of
constructing HTTP requests to providers directly.

Usage::

    from backend.llm.gateway import call

    response = call("meta_planner", [
        {"role": "system", "content": "..."},
        {"role": "user", "content": "..."},
    ], max_tokens=4096)

    content = response["choices"][0]["message"]["content"]
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

# ── Configuration ──────────────────────────────────────────────────────────

LITELLM_BASE = os.environ.get("LITELLM_BASE", "http://localhost:4000/v1")

# Role name → env var that holds the LiteLLM virtual key for that role
ROLE_KEY_ENV: dict[str, str] = {
    "meta_planner": "LITELLM_KEY_PLANNING",
    "plan_evaluator": "LITELLM_KEY_PLANNING",
    "l2_judge": "LITELLM_KEY_EVALUATION",
    "l3_jury": "LITELLM_KEY_EVALUATION",
    "execution": "LITELLM_KEY_EXECUTION",
    "plan_brain": "LITELLM_KEY_PLANNING",
    "aionui_orchestrator": "LITELLM_KEY_PLANNING",
}

# Role name → LiteLLM model group name (defined in config.yaml)
ROLE_MODEL: dict[str, str] = {
    "meta_planner": "deepseek-planning",
    "plan_evaluator": "deepseek-planning",
    "l2_judge": "judge",
    "l3_jury": "judge",
    "execution": "gptoss-exec",
    "plan_brain": "deepseek-planning",
    "aionui_orchestrator": "deepseek-planning",
}

# Per-role usage accumulator (for per-run token budget tracking)
_USAGE: dict[str, int] = {}


def get_usage(role: str | None = None) -> dict[str, int]:
    """Return accumulated token usage.

    Args:
        role: If provided, returns only that role's usage. Otherwise returns all.

    Returns:
        Dict mapping role → total_tokens.
    """
    if role:
        return {role: _USAGE.get(role, 0)}
    return dict(_USAGE)


def reset_usage() -> None:
    """Reset accumulated usage (call at the start of each run)."""
    _USAGE.clear()


def call(
    role: str,
    messages: list[dict[str, Any]],
    **kwargs: Any,
) -> dict[str, Any]:
    """Send a chat-completion request through the LiteLLM gateway.

    Args:
        role: One of the roles defined in ``ROLE_MODEL`` / ``ROLE_KEY_ENV``.
        messages: OpenAI-format message list
            ``[{"role": "system|user", "content": "..."}]``.
        **kwargs: Extra fields passed directly in the request body
            (e.g. ``max_tokens``, ``temperature``).

    Returns:
        The full OpenAI chat-completion response dict.

    Raises:
        RuntimeError: If the gateway is unreachable or returns an error.
    """
    key_env = ROLE_KEY_ENV.get(role)
    api_key = os.environ.get(key_env, "") if key_env else ""
    model = ROLE_MODEL.get(role, "deepseek-planning")

    base = LITELLM_BASE.rstrip("/")
    endpoint = f"{base}/chat/completions" if not base.endswith("/chat/completions") else base

    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
    }
    body.update(kwargs)

    headers = {
        "Content-Type": "application/json",
        "User-Agent": "conductor-gateway/1.0",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    logger.info(
        "gateway.call role=%s model=%s endpoint=%s messages=%d",
        role, model, endpoint, len(messages),
    )

    try:
        req = urllib.request.Request(
            endpoint,
            data=json.dumps(body).encode(),
            headers=headers,
        )
        with urllib.request.urlopen(req, timeout=kwargs.get("timeout", 120)) as resp:
            data: dict[str, Any] = json.loads(resp.read().decode())
    except Exception as exc:
        raise RuntimeError(
            f"LiteLLM gateway call failed for role={role!r} model={model!r}: {exc}"
        ) from exc

    # Track usage for per-run token budget
    usage = data.get("usage") or {}
    total = usage.get("total_tokens", 0) or 0
    _USAGE[role] = _USAGE.get(role, 0) + total

    return data
