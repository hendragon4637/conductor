"""Bounded remediation with delta-gated retries.

When the evaluator gate rejects a node (L1 or L2), this module:
1. Builds structured feedback (what/why/how + evidence).
2. Checks whether to continue (delta-gated: improving, not regressing/plateau).
3. Builds a fix-forward remediation brief for the agent.
4. Tracks attempt history for delta-gating.

Hard cap: MAX_ATTEMPTS = 4.
Continue while improving: L1 passed-set grows OR L2 score gains >= MIN_IMPROVE (0.05).
Stop on: regression (lost prior L1 pass / L2 dropped), plateau, or hard cap.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

HARD_CAP = 4
MIN_IMPROVE = 0.05


# ── Attempt history for delta-gating ────────────────────────────────────────


@dataclass
class AttemptSnapshot:
    """Snapshot of a single remediation attempt for delta comparison."""
    l1_passed_ids: list[str] = field(default_factory=list)
    l2_score: float | None = None


def should_continue(history: list[AttemptSnapshot]) -> tuple[bool, str]:
    """Delta-gated check: continue while improving, stop on regression/plateau.

    Args:
        history: List of AttemptSnapshots from past gate decisions, in order.

    Returns:
        (continue_bool, reason) — reason is "hard cap", "regression",
        "plateau", or "improving".
    """
    if len(history) >= HARD_CAP:
        return False, f"hard cap ({HARD_CAP})"

    if len(history) < 2:
        return True, "first attempt"

    prev, cur = history[-2], history[-1]

    # L1 regression: lost a prior pass
    cur_set = set(cur.l1_passed_ids)
    prev_set = set(prev.l1_passed_ids)
    l1_regressed = not cur_set >= prev_set

    # L1 improvement: set grew without losing
    l1_improved = cur_set >= prev_set and len(cur_set) > len(prev_set)

    # L2 delta
    prev_score = prev.l2_score or 0.0
    cur_score = cur.l2_score or 0.0
    l2_delta = cur_score - prev_score

    if l1_regressed or l2_delta < 0:
        return False, "regression"
    if l1_improved or l2_delta >= MIN_IMPROVE:
        return True, "improving"
    return False, "plateau"


# ── Feedback construction ───────────────────────────────────────────────────


def build_feedback(decision: Any) -> dict:
    """Build structured verbal feedback with what/why/how + evidence.

    Accepts either a ``GateDecision`` dataclass or a raw reason dict.

    Returns:
        Dict with:
        - failed_checks: list of {tier, id, what, why, how, evidence}
        - reflection: concise summary of what to fix
    """
    # Handle GateDecision dataclass
    if hasattr(decision, "l1_feedback"):
        failed = []
        passed = []
        l1_fb = getattr(decision, "l1_feedback", []) or []
        l2_fb = getattr(decision, "l2_feedback", []) or []

        for item in l1_fb:
            failed.append({
                "tier": "L1",
                "id": item.get("check_id", "?"),
                "what": item.get("what", "L1 check failed"),
                "why": item.get("why", ""),
                "how": item.get("how", "Fix the issue"),
                "evidence": item.get("evidence", ""),
            })

        for item in l2_fb:
            entry = {
                "tier": "L2",
                "id": item.get("check_id", "?"),
                "what": item.get("what", "L2 rubric not met"),
                "why": item.get("why") or item.get("explanation", ""),
                "how": "Address the rubric item in the implementation",
                "evidence": item.get("evidence", "") or item.get("explanation", ""),
            }
            if item.get("criteria_met", True):
                passed.append(entry)
            else:
                failed.append(entry)

        reflection = _build_reflection(failed)
        return {"failed_checks": failed, "passed_checks": passed, "reflection": reflection}

    # Handle raw reason dict (legacy path)
    return _build_feedback_from_dict(decision)


def _build_feedback_from_dict(decision: dict) -> dict:
    """Legacy path: build feedback from a raw reason dict."""
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
                        "what": item.get("criterion", "L1 check failed"),
                        "why": item.get("check_cmd", ""),
                        "how": "Fix the issue indicated by the check",
                        "evidence": str(item.get("output", ""))[:300],
                    })
            elif isinstance(item, (list, tuple)):
                cid, ok, tail = item[0], item[1], item[2] if len(item) > 2 else ""
                if not ok:
                    failed.append({
                        "tier": "L1",
                        "id": cid,
                        "what": "L1 check failed",
                        "why": "",
                        "how": "Fix the issue",
                        "evidence": str(tail)[:300],
                    })

    elif layer == "L2" and isinstance(detail, list):
        for j in detail:
            if isinstance(j, dict):
                if not j.get("criteria_met", True):
                    failed.append({
                        "tier": "L2",
                        "id": j.get("check_id", "?"),
                        "what": j.get("rubric_item", "L2 rubric not met"),
                        "why": j.get("explanation", ""),
                        "how": "Address the rubric item",
                        "evidence": j.get("explanation", ""),
                    })
            elif hasattr(j, "criteria_met"):
                if not j.criteria_met:
                    failed.append({
                        "tier": "L2",
                        "id": j.check_id,
                        "what": "L2 rubric not met",
                        "why": j.explanation or "",
                        "how": "Address the rubric item",
                        "evidence": j.explanation or "",
                    })

    reflection = _build_reflection(failed)
    return {"failed_checks": failed, "reflection": reflection}


def _build_reflection(failed: list[dict]) -> str:
    """Build concise reflection from failed checks list."""
    if not failed:
        return "No specific failures identified; review and retry."
    parts = []
    for f in failed:
        tid = f.get("tier", "?")
        cid = f.get("id", "?")
        why = f.get("why", "") or f.get("what", "")
        if why:
            parts.append(f"  [{tid}] {cid}: {why}")
        else:
            parts.append(f"  [{tid}] {cid}")
    return "\n".join(parts)


# ── Remediation brief ───────────────────────────────────────────────────────


def build_remediation_brief(
    original_task: str,
    success_criterion: str,
    feedback: dict,
    l1_flagged: bool = False,
) -> str:
    """Build the fix-forward brief for a remediation attempt.

    Includes original goal, failed checks (with what/why/how/evidence),
    and instruction to fix existing work, not start over.
    """
    lines = [
        f"GOAL (retry): {original_task}",
        "",
        "YOUR PREVIOUS ATTEMPT FAILED THESE CHECKS:",
    ]
    for fc in feedback.get("failed_checks", []):
        tid = fc.get("tier", "?")
        cid = fc.get("id", "?")
        what = fc.get("what", "")
        why = fc.get("why", "")
        how = fc.get("how", "")
        evidence = fc.get("evidence", "")
        lines.append(f"  [{tid}] {cid}")
        if what:
            lines.append(f"      What: {what}")
        if why:
            lines.append(f"      Why: {why}")
        if how:
            lines.append(f"      How: {how}")
        if evidence:
            lines.append(f"      Evidence: {evidence[:300]}")

    passed = feedback.get("passed_checks", [])
    if passed:
        lines.append("")
        lines.append("KEEP THESE WORKING (the following passed — do NOT regress on them):")
        for pc in passed:
            tid = pc.get("tier", "?")
            cid = pc.get("id", "?")
            why = pc.get("why", "")
            lines.append(f"  [{tid}] {cid}")
            if why:
                lines.append(f"      Why: {why[:200]}")

    if l1_flagged:
        lines.append("")
        lines.append("NOTE: L2 evaluation suggests your work is substantively correct,")
        lines.append("but a deterministic L1 check is blocking. This L1 check may be")
        lines.append("mis-specified. Prioritize satisfying the exact L1 check command")
        lines.append("and paths shown above.")

    lines.append("")
    lines.append(f"WHAT TO FIX: {feedback.get('reflection', 'Review and fix the issues above.')}")
    lines.append("")
    lines.append("If your previous work appears correct but evaluator feedback conflicts with your assessment, prioritize the evaluator feedback. Make the work satisfy the exact failed check, including its command, working directory, and checked paths.")
    lines.append("The code from your previous attempt is in this worktree. FIX IT — do NOT start over.")
    lines.append(f"SUCCESS: {success_criterion}")
    return "\n".join(lines)


# ── Legacy backward-compatible wrappers ──────────────────────────────────────

DEFAULT_ATTEMPT_CAP = 2


def _render_fix_task(reason: dict) -> str:
    """Legacy: render a human-readable fix instruction from a reason dict."""
    fb = _build_feedback_from_dict(reason)
    return fb.get("reflection", "Fix the issues above.")


def insert_remediation(
    plan_id: str,
    failed_node: dict,
    decision: dict,
    attempt_cap: int = DEFAULT_ATTEMPT_CAP,
    existing_chunks: list | None = None,
) -> dict | None:
    """Legacy: insert a remediation node via decompose_or_update.

    Deprecated in favor of delta-gated remediation in supervisor.
    """
    from backend.planning.decompose import decompose_or_update

    remediation_count = failed_node.get("remediation_count", 0)
    if remediation_count >= attempt_cap:
        logger.warning(
            "Legacy insert_remediation: cap reached for %s (%d/%d)",
            failed_node.get("id"), remediation_count, attempt_cap,
        )
        return None

    feedback = _build_feedback_from_dict(decision)
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
        failed_node["remediation_count"] = remediation_count + 1
        logger.info(
            "Legacy insert_remediation: attempt %d/%d for %s",
            remediation_count + 1, attempt_cap, failed_node.get("id"),
        )
        return result
    except Exception as e:
        logger.exception("Legacy insert_remediation failed: %s", e)
        return None
