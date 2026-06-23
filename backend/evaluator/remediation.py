"""Bounded remediation — inserts a fix node via the existing decompose lifecycle.

When the evaluator gate rejects a node (L1 or L2), this module builds a
remediation node with verbal feedback (reflection) from the failing checks.
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
        for item in detail:
            if isinstance(item, dict):
                check_id = item.get("check_id", "?")
                ok = item.get("ok", False)
                tail = str(item.get("output", ""))
                command = item.get("check_cmd", "")
                criterion = item.get("criterion", "")
            else:
                check_id, ok, tail = item
                command = ""
                criterion = ""
            status = "PASSED" if ok else "FAILED"
            parts.append(f"  [{status}] {check_id}")
            if criterion:
                parts.append(f"    Criterion: {criterion}")
            if command:
                parts.append(f"    Command: {command}")
            if not ok:
                parts.append(f"    Output: {tail[:300]}")
        return "\n".join(parts)
    if isinstance(detail, dict):
        parts.append(str(detail))
    return "\n".join(parts)


def build_feedback(decision: dict) -> dict:
    """Build structured verbal feedback from a gate decision.

    Returns a dict with:
      - failed_checks: list of {tier, id, detail, why}
      - reflection: concise, specific summary of what to fix
    """
    layer = decision.get("layer", "?")
    detail = decision.get("detail", [])
    failed = []

    if layer == "L1" and isinstance(detail, list):
        for item in detail:
            if isinstance(item, dict):
                if item.get("ok") is False:
                    failed.append({
                        "tier": "L1",
                        "id": item.get("check_id", "?"),
                        "criterion": item.get("criterion", ""),
                        "command": item.get("check_cmd", ""),
                        "worktree": item.get("worktree", ""),
                        "detail": str(item.get("output", ""))[:300],
                    })
                continue
            check_id, ok, tail = item
            if not ok:
                failed.append({
                    "tier": "L1",
                    "id": check_id,
                    "detail": tail[:300],
                })
    elif layer == "L2" and isinstance(detail, list):
        for j in detail:
            if isinstance(j, dict):
                if not j.get("criteria_met", True):
                    failed.append({
                        "tier": "L2",
                        "id": j.get("check_id", "?"),
                        "why": j.get("explanation", "no explanation"),
                    })
            elif isinstance(j, (list, tuple)) and len(j) >= 2:
                check_id, ok = j[0], j[1]
                if not ok:
                    failed.append({"tier": "L2", "id": str(check_id)})
            elif hasattr(j, "criteria_met"):
                if not j.criteria_met:
                    failed.append({
                        "tier": "L2",
                        "id": j.check_id,
                        "why": j.explanation or "no explanation",
                    })

    # Build a concise reflection
    if not failed:
        reflection = "No specific failures identified; review and retry."
    else:
        parts = []
        for f in failed:
            if f.get("why"):
                parts.append(f"  - {f['id']}: {f['why']}")
            elif f.get("command"):
                parts.append(f"  - {f['id']}: failed `{f['command']}`")
            elif f.get("detail"):
                parts.append(f"  - {f['id']}: check failed")
            else:
                parts.append(f"  - {f['id']}")
        reflection = "\n".join(parts)

    return {"failed_checks": failed, "reflection": reflection}


def build_remediation_brief(
    original_task: str,
    success_criterion: str,
    feedback: dict,
) -> str:
    """Build the fix-forward brief for a remediation attempt.

    Includes original goal, failed checks, and what to fix.
    Instructs the agent to fix existing work, not start over.
    """
    lines = [
        f"GOAL (retry): {original_task}",
        "",
        "YOUR PREVIOUS ATTEMPT FAILED THESE CHECKS:",
    ]
    for fc in feedback.get("failed_checks", []):
        tid = fc.get("tier", "?")
        cid = fc.get("id", "?")
        why = fc.get("why") or fc.get("detail", "")
        lines.append(f"  [{tid}] {cid}: {why}")
        if fc.get("criterion"):
            lines.append(f"      Criterion: {fc['criterion']}")
        if fc.get("worktree"):
            lines.append(f"      Worktree checked: {fc['worktree']}")
        if fc.get("command"):
            lines.append(f"      Exact command that failed: {fc['command']}")

    lines.append("")
    lines.append(f"WHAT TO FIX: {feedback.get('reflection', 'Review and fix the issues above.')}")
    lines.append("")
    lines.append("If your previous work appears correct but evaluator feedback conflicts with your assessment, prioritize the evaluator feedback. Make the work satisfy the exact failed check, including its command, working directory, and checked paths.")
    lines.append("The code from your previous attempt is in this worktree. FIX IT — do NOT start over.")
    lines.append(f"SUCCESS: {success_criterion}")
    return "\n".join(lines)


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

    # Build verbal feedback from the gate decision
    feedback = build_feedback(decision)
    original_task = (failed_node.get("task") or {}).get("text", "")
    success_criterion = (failed_node.get("success") or {}).get("text", "")

    brief = build_remediation_brief(original_task, success_criterion, feedback)

    payload = {
        "members": failed_node.get("members", ["opencode:backend-executor"]),
        "depends_on": [failed_node.get("id", "?")],
        "task": brief,
        "success_criterion": f"Fix the failures: {feedback.get('reflection', 'see above')[:200]}",
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
