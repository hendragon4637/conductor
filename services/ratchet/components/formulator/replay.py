"""Formulator replay — run the live formulator with a prompt override.

Imports ONLY the formulation function from the live codebase. ``formulate()``
is pure: no DB writes, no bus emits, no worktrees — replay is side-effect-free
by construction. The ``raw_capture`` callback retains the model's raw output
for observability.
"""
from __future__ import annotations

import time
from typing import Any

from backend.planning.meta_planner.goal_formulator import build_standards_menu, formulate
from services.ratchet.components.formulator import metrics


def replay(
    item: Any,
    prompt_template: str,
    model_alias: str | None = None,
) -> dict[str, Any]:
    raw: list[str] = []
    start = time.monotonic()
    mg = formulate(
        raw_input=item.input["raw_input"],
        origin=item.input.get("origin") or "human",
        project_id=item.input.get("project_id"),
        prompt_override=prompt_template,
        raw_capture=raw.append,
    )
    duration_ms = int((time.monotonic() - start) * 1000)
    return {
        "standards": [c.standard_slug for c in mg.components],
        "subdirs": [c.subdir for c in mg.components],
        "clarify": mg.needs_clarification,
        "estimated_nodes": mg.estimated_node_count,
        "raw_response": raw[-1] if raw else None,
        "duration_ms": duration_ms,
        "model": model_alias or "deepseek-planning",
    }
