"""Bounded remediation — inserts a fix node via the existing decompose lifecycle.

When the evaluator gate rejects a node (L1 or L2), this module builds a
remediation node that depends on the failed node and carries the same checks.
It reuses ``decompose_or_update("append_node")`` — no new orchestration.
"""
from __future__ import annotations

import logging
from typing import Any

from backend.planning.decompose import decompose_or_update

logger = logging.getLogger(__name__)

# Default maximum remediation attempts before escalating to human
DEFAULT_ATTEMPT_CAP = 2


def _render_fix_task(reason: dict) -> str:
    """Render a human-readable fix instruction from the gate decision reason."""
    parts = ["Fix the following issues:"]
    layer = reason.get("layer", "?")
    detail = reason.get("detail", [])
    if layer == "L1" and isinstance(detail, list):
        for check_id, ok, tail in detail:
            status = "PASSED" if ok else "FAILED"
            parts.append(f"  [{status}] {check_id}")
            if not ok:
                parts.append(f"    Output: {tail[:300]}")
        return "\n".join(parts)
    if isinstance(detail, dict):
        parts.append(str(detail))
    return "\n".join(parts)


def insert_remediation(
    plan_id: str,
    failed_node: dict[str, Any],
    decision: dict,
    attempt_cap: int = DEFAULT_ATTEMPT_CAP,
    existing_chunks: list | None = None,
) -> dict[str, Any] | None:
    """Insert a bounded remediation node into the plan.

    Args:
        plan_id: The plan to modify.
        failed_node: The node dict that failed evaluation (must have ``id``,
                     ``members``, ``checks``).
        decision: The gate decision reason dict.
        attempt_cap: Max remediation attempts before escalating.
        existing_chunks: Current chunks list for incremental append.

    Returns:
        The new DecomposedPlan if remediation was inserted, or None if
        ``attempt_cap`` was reached (escalation to human).

    Side effects:
        - Appends a remediation node to the plan via ``decompose_or_update``.
        - Increments ``remediation_count`` on the failed node (mutates in-place
          if it's a dict, or the caller must track it).
    """
    remediation_count = failed_node.get("remediation_count", 0)
    if remediation_count >= attempt_cap:
        logger.warning(
            "Remediation cap reached for node %s (%d/%d) — escalating to human",
            failed_node.get("id"), remediation_count, attempt_cap,
        )
        return None

    payload = {
        "members": failed_node.get("members", ["opencode:backend-executor"]),
        "depends_on": [failed_node.get("id", "?")],
        "task": _render_fix_task(decision),
        "success_criterion": f"Fix the failures: {_render_fix_task(decision)[:200]}",
    }

    try:
        result = decompose_or_update(
            plan_id=plan_id,
            source="append_node",
            payload=payload,
            existing_chunks=existing_chunks,
        )
        # Increment remediation count on the failed node
        failed_node["remediation_count"] = remediation_count + 1
        logger.info(
            "Inserted remediation node for %s (attempt %d/%d)",
            failed_node.get("id"), remediation_count + 1, attempt_cap,
        )
        return result
    except Exception as e:
        logger.exception("Failed to insert remediation node: %s", e)
        return None
