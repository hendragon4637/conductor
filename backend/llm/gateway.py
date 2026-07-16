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
    "planning": "LITELLM_KEY_PLANNING",
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
    "planning": "deepseek-planning",
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

    # Log full request for debugging (omit Authorization header)
    log_req_body = {**body, "messages": [
        {**m, "content": (m.get("content", "")[:200] + "..." if len(m.get("content", "")) > 200 else m.get("content", ""))}
        for m in body.get("messages", [])
    ]}
    logger.debug(
        "LLM REQUEST role=%s model=%s body=%s",
        role, model, json.dumps(log_req_body),
    )

    try:
        req = urllib.request.Request(
            endpoint,
            data=json.dumps(body).encode(),
            headers=headers,
        )
        with urllib.request.urlopen(req, timeout=kwargs.get("timeout", 120)) as resp:
            data: dict[str, Any] = json.loads(resp.read().decode())
    except urllib.error.HTTPError as http_exc:
        # Preserve retry-after and status code from 429/rate-limit responses
        ra = http_exc.headers.get("Retry-After", "")
        body = http_exc.read().decode(errors="replace")[:500]
        raise RuntimeError(
            f"LiteLLM gateway call failed for role={role!r} model={model!r}: "
            f"HTTP {http_exc.code} retry-after={ra} body={body}"
        ) from http_exc
    except Exception as exc:
        raise RuntimeError(
            f"LiteLLM gateway call failed for role={role!r} model={model!r}: {exc}"
        ) from exc

    # Log response for debugging (truncate long content)
    log_resp = {
        "model": data.get("model", "?"),
        "usage": data.get("usage"),
        "choices": [
            {
                "finish_reason": c.get("finish_reason"),
                "content_preview": (c["message"]["content"] or "")[:200],
            }
            for c in data.get("choices", [])
        ],
    }
    logger.debug(
        "LLM RESPONSE role=%s status=200 body=%s",
        role, json.dumps(log_resp),
    )

    # Track usage for per-run token budget
    usage = data.get("usage") or {}
    total = usage.get("total_tokens", 0) or 0
    _USAGE[role] = _USAGE.get(role, 0) + total

    return data
