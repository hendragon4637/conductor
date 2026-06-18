"""Brain model selector — config-driven primary/fallback resolution.

Loads model configuration from ``config/brain_models.json``.
Policy:
1. Try primary (hosted: OpenRouter / NVIDIA NIM) for speed.
2. Fallback through the list if primary unreachable.
3. Local OVMS as the final floor so it never hard-fails.

Privacy: golden-set calls stay local (never sent to hosted tiers).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "brain_models.json"


def _load_config() -> dict[str, Any]:
    if _CONFIG_PATH.exists():
        with open(_CONFIG_PATH) as f:
            return json.load(f)
    return {}


def _provider_available(cand: dict[str, Any]) -> bool:
    """Check whether a model candidate is usable.

    - If it has an ``api_key_env``, the env var must be set and non-empty.
    - No connectivity check (expensive); missing key is the gating factor.
    """
    key_env = cand.get("api_key_env")
    if key_env:
        val = os.environ.get(key_env, "").strip()
        if not val:
            return False
    return True


def get_brain_model(role: str = "plan_brain") -> dict[str, Any]:
    """Resolve a model for *role* (plan_brain | evaluator | golden_set).

    Returns a dict with ``provider``, ``model``, ``base_url``.
    Raises ``RuntimeError`` if no model is available.
    """
    cfg = _load_config()

    # golden_set always stays local — never route to hosted tiers
    if role == "golden_set":
        gs = cfg.get("golden_set")
        if gs:
            return dict(gs)
        return {
            "provider": "local_ovms",
            "model": "qwen3-8b-int4-ov",
            "base_url": "http://localhost:8001/v3",
        }

    role_cfg = cfg.get(role, {})
    candidates = [role_cfg.get("primary")] if role_cfg.get("primary") else []
    candidates.extend(role_cfg.get("fallback", []))

    for cand in candidates:
        if not cand:
            continue
        if _provider_available(cand):
            return {
                "provider": cand.get("provider", "unknown"),
                "model": cand.get("model", "unknown"),
                "base_url": cand.get("base_url", "").rstrip("/"),
            }

    raise RuntimeError(f"no model available for role {role!r}")


def select_brain_model(task_hint: str | None = None) -> dict[str, Any]:
    """Legacy-compatible wrapper. Returns ``{provider, model, endpoint, is_frontier}``.

    Uses ``get_brain_model("plan_brain")`` under the hood.
    """
    resolved = get_brain_model("plan_brain")
    is_frontier = resolved["provider"] not in ("local_ovms", "local")
    endpoint = resolved["base_url"]
    if not endpoint.endswith("/chat/completions"):
        endpoint += "/v1/chat/completions"
    return {
        "provider": resolved["provider"],
        "model": resolved["model"],
        "endpoint": endpoint,
        "is_frontier": is_frontier,
    }


def budget_available() -> bool:
    """Check if daily brain budget is not exceeded.

    Reads from a simple counter file.  Returns True if no budget set or
    budget remaining.
    """
    budget_str = os.environ.get("BRAIN_BUDGET_TOKENS_DAY")
    if not budget_str:
        return True

    max_tokens = int(budget_str)
    counter_path = os.environ.get(
        "BRAIN_COUNTER_PATH",
        "/tmp/brain_token_counter.txt",
    )

    used = 0
    try:
        with open(counter_path) as f:
            used = int(f.read().strip())
    except (FileNotFoundError, ValueError):
        pass

    return used < max_tokens
