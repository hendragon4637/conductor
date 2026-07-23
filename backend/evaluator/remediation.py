"""Bounded remediation with patience-based retries.

When the evaluator gate rejects a node (L1 or L2), this module:
1. Builds structured feedback (what/why/how + evidence).
2. Checks whether to continue using patience-based early stopping.
3. Builds a fix-forward remediation brief for the agent.
4. Tracks attempt history for best-so-far comparison.

Hard cap: REMEDIATION_HARD_CAP, default 10.
Continue until REMEDIATION_PATIENCE consecutive attempts fail to beat best score by REMEDIATION_MIN_DELTA.
Stop on: pass, patience exhaustion, or hard cap.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_PATIENCE = 3
DEFAULT_HARD_CAP = 20
DEFAULT_MIN_DELTA = 0.02


# ── Attempt history for delta-gating ────────────────────────────────────────


@dataclass
class AttemptSnapshot:
    """Snapshot of a single remediation attempt for patience comparison."""
    l1_passed_ids: list[str] = field(default_factory=list)
    l2_score: float | None = None
    gate_outcome: str | None = None


def should_continue(history: list[AttemptSnapshot]) -> tuple[bool, str]:
    """Patience-based check — separate L1 and L2 tracking.

    L1 patience tracks improvement in number of passing L1 checks.
    L2 patience tracks improvement in L2 score — only counts attempts
    where L2 was actually evaluated (l2_score is not None).

    Both must be exhausted (or the only active one) to stop.

    Args:
        history: List of AttemptSnapshots from past gate decisions, in order.

    Returns:
        (continue_bool, reason) — reason is "passed", "hard_cap",
        "patience_exhausted", or "within_patience".
    """
    if not history:
        return True, "within_patience"

    if history[-1].gate_outcome == "done":
        return False, "passed"

    hard_cap = int(os.environ.get("REMEDIATION_HARD_CAP", str(DEFAULT_HARD_CAP)))
    if len(history) >= hard_cap:
        return False, "hard_cap"

    patience = int(os.environ.get("REMEDIATION_PATIENCE", str(DEFAULT_PATIENCE)))
    min_delta = float(os.environ.get("REMEDIATION_MIN_DELTA", str(DEFAULT_MIN_DELTA)))

    # ── L2 patience: only attempts where L2 was actually evaluated ──
    l2_history = [h for h in history if h.l2_score is not None]
    best_l2 = 0.0
    trailing_l2 = 0
    for attempt in l2_history:
        score = attempt.l2_score or 0.0
        if score > best_l2 + min_delta:
            best_l2 = score
            trailing_l2 = 0
        else:
            trailing_l2 += 1

    # ── L1 patience: all attempts, track number of passing L1 checks ──
    best_l1 = 0
    trailing_l1 = 0
    for attempt in history:
        l1_count = len(attempt.l1_passed_ids)
        if l1_count > best_l1:
            best_l1 = l1_count
            trailing_l1 = 0
        else:
            trailing_l1 += 1

    # Determine exhaustion — L2 takes priority when it exists.
    # When L2 data is present, L2 improvement drives the decision:
    # if L2 is still improving we keep going regardless of L1.
    # L1-only fallback when no L2 data exists.
    l2_active = len(l2_history) > 0
    l1_active = len(history) > 0

    l1_exhausted = trailing_l1 >= patience
    l2_exhausted = l2_active and trailing_l2 >= patience

    if l2_active:
        # L2 is primary gate when data exists
        if l2_exhausted:
            return False, "patience_exhausted"
    elif l1_active:
        # No L2 data — fall back to L1 patience
        if l1_exhausted:
            return False, "patience_exhausted"

    return True, "within_patience"


def best_score(history: list[AttemptSnapshot]) -> float:
    """Best L2 score across attempts where L2 was actually evaluated."""
    l2_scores = [attempt.l2_score for attempt in history if attempt.l2_score is not None]
    return max(l2_scores) if l2_scores else 0.0


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
            })

        for item in l2_fb:
            entry = {
                "tier": "L2",
                "id": item.get("check_id", "?"),
                "what": item.get("what", "L2 rubric not met"),
                "why": item.get("why") or item.get("explanation", ""),
                "how": item.get("how", "Address the rubric item in the implementation"),
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
                    })

    elif layer == "L2" and isinstance(detail, list):
        for j in detail:
            if isinstance(j, dict):
                if not j.get("criteria_met", True):
                    failed.append({
                        "tier": "L2",
                        "id": j.get("check_id", "?"),
                        "what": j.get("what") or j.get("rubric_item", "L2 rubric not met"),
                        "where": j.get("where", ""),
                        "why": j.get("why") or j.get("explanation", ""),
                        "how": j.get("how") or "Address the rubric item",
                    })
            elif hasattr(j, "criteria_met"):
                fb = getattr(j, "feedback_raw", None) or {}
                if not j.criteria_met:
                    failed.append({
                        "tier": "L2",
                        "id": j.check_id,
                        "what": fb.get("what", "L2 rubric not met"),
                        "where": fb.get("where", ""),
                        "why": fb.get("why") or j.explanation or "",
                        "how": fb.get("how") or "Address the rubric item",
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
        where = f.get("where", "")
        where_part = f" @ {where}" if where else ""
        if why:
            parts.append(f"  [{tid}] {cid}{where_part}: {why}")
        else:
            parts.append(f"  [{tid}] {cid}{where_part}")
    return "\n".join(parts)


# ── Remediation brief ───────────────────────────────────────────────────────


def build_remediation_feedback(failed_checks: list[dict]) -> str:
    """Render structured per-dim feedback as WHAT/WHERE/WHY/FIX blocks.

    Each failed check is expected to carry ``{what, where, why, how}``
    keys from the L2 judge's structured feedback.
    """
    lines = ["FAILED CRITERIA (fix ONLY these, file-targeted):"]
    for fc in failed_checks:
        tid = fc.get("tier", "?")
        cid = fc.get("id", "?")
        what = fc.get("what", "")
        where = fc.get("where", "")
        why = fc.get("why", "")
        how = fc.get("how", "")
        lines.append(f"- [{tid}/{cid}]")
        if what:
            lines.append(f"  WHAT: {what}")
        if where:
            lines.append(f"  WHERE: {where}")
        if why:
            lines.append(f"  WHY: {why}")
        if how:
            lines.append(f"  FIX: {how}")
    return "\n".join(lines)


def build_remediation_brief(
    original_task: str,
    success_criterion: str,
    feedback: dict,
    l1_flagged: bool = False,
) -> str:
    """Build the fix-forward brief for a remediation attempt.

    Includes original goal, failed checks (with what/where/why/how plus
    degraded warnings), and instruction to fix existing work, not start over.

    If the node carried ``acceptance_criteria`` and feedback references
    them, the brief pairs each failed criterion verbatim with the judge finding.
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
        where = fc.get("where", "")
        why = fc.get("why", "")
        how = fc.get("how", "")
        degraded = fc.get("_degraded", False)
        lines.append(f"  [{tid}] {cid}")
        if what:
            lines.append(f"      What: {what}")
        if where:
            lines.append(f"      Where: {where}")
        if why:
            lines.append(f"      Why: {why}")
        if how:
            lines.append(f"      How: {how}")
        if degraded:
            lines.append(f"      NOTE: (feedback low-confidence — verify against the criterion text yourself)")

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
