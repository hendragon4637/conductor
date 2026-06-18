"""Multi-model ensemble — optional, disabled by default.

If planning errors become a measured failure source, enable this module
which runs N similar-strength frontier models and uses a judge to
synthesize the best plan.

Leave DISABLED by default — single frontier model is simpler and usually
sufficient.
"""
from __future__ import annotations

from typing import Any, Callable


def propose_plan_ensemble(
    user_intent: str,
    context: dict[str, Any] | None = None,
    available_agent_configs: list[dict[str, Any]] | None = None,
    multimodal_refs: list[str] | None = None,
    llm_call: Callable[[str], str] | None = None,
) -> dict[str, Any] | None:
    """Run N models in parallel, judge selects best valid plan.

    Args:
        user_intent: Free-text description of what the user wants done.
        context: Optional context dict.
        available_agent_configs: List of agent config dicts.
        multimodal_refs: Optional paths or URLs to reference images.
        llm_call: Function that takes a prompt and returns a JSON string.

    Returns:
        A validated Plan dict, or None if ensemble fails.
    """
    # Stub — not enabled by default
    return None
