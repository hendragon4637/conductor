from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

STEER_HARD_CAP = 5


def build_steering_brief(
    original_task: str,
    success_criterion: str,
    feedback: dict,
    steering_count: int = 0,
) -> str:
    """Build a fix-forward brief for a steering attempt.

    Same feedback structure as ``build_remediation_brief`` but with steering-
    appropriate language: the agent is already in the existing conversation,
    so there is no "start over" prohibition — the agent sees the brief as a
    continuation message in the same session.
    """
    from backend.evaluator.remediation import (
        build_remediation_feedback as _render_failed,
    )

    lines = [
        f"GOAL (steering attempt {steering_count + 1}/{STEER_HARD_CAP}): {original_task}",
        "",
        "YOUR PREVIOUS ATTEMPT FAILED THESE CHECKS:",
    ]
    for fc in feedback.get("failed_checks", []):
        tid = fc.get("tier", "?")
        cid = fc.get("id", "?")
        what = fc.get("what", "")
        where = fc.get("where", "")
        why = fc.get("why", "")
        how = fc.get("how", "")
        lines.append(f"  [{tid}] {cid}")
        if what:
            lines.append(f"      What: {what}")
        if where:
            lines.append(f"      Where: {where}")
        if why:
            lines.append(f"      Why: {why}")
        if how:
            lines.append(f"      How: {how}")

    passed = feedback.get("passed_checks", [])
    if passed:
        lines.append("")
        lines.append("KEEP THESE WORKING (do NOT regress on them):")
        for pc in passed:
            tid = pc.get("tier", "?")
            cid = pc.get("id", "?")
            why = pc.get("why", "")
            lines.append(f"  [{tid}] {cid}")
            if why:
                lines.append(f"      Why: {why[:200]}")

    lines.append("")
    lines.append(
        "Your previous code is in the worktree. Fix the failures above. "
        "Keep what already works. Do NOT start over."
    )
    lines.append(f"SUCCESS: {success_criterion}")
    return "\n".join(lines)
