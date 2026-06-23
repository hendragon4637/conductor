"""Evaluator gate — decides whether a node should advance or remediate.

L1 (deterministic) -> L2 (rubric judge).
Strict both-pass: ``done`` requires L1 AND L2 to pass.
False-fail escalation: if L1 fails on a remediation attempt but the executor
made changes and L1 did not improve, an L2 probe is run to disambiguate.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class GateDecision:
    """Decision from the evaluator gate.

    ``action`` is one of:
    - ``"done"``: both L1 and L2 passed — commit the node.
    - ``"remediate"``: one or both layers failed — spawn remediation attempt.

    L1 results:
    - ``l1_passed_ids``: list of L1 check IDs that passed.
    - ``l1_feedback``: per-failed-check detail (what/why/how/evidence).
    - ``l1_flagged``: true if L2 probe passed but L1 check is suspected bad.

    L2 results:
    - ``l2_passed``: true if L2 score >= threshold.
    - ``goal_review``: the L2 score (0.0-1.0).
    - ``l2_feedback``: per-rubric-item reasoning.
    """
    action: str  # "done" | "remediate"
    l1_passed_ids: list[str] = field(default_factory=list)
    l1_feedback: list[dict[str, Any]] = field(default_factory=list)
    l1_flagged: bool = False
    l2_passed: bool = False
    goal_review: float | None = None
    l2_feedback: list[dict[str, Any]] = field(default_factory=list)


def evaluate_gate(
    check_list: list,
    worktree: str,
    l2_fn: Callable | None = None,
    threshold: float = 0.7,
    prev_l1_passed_ids: list[str] | None = None,
    has_changes_since_prev: bool = False,
) -> GateDecision:
    """Run evaluator gates for a node.

    Order: L1 (deterministic) -> L2 (rubric judge).
    Strict rule: ``action == "done"`` requires both L1 and L2 to pass.

    False-fail escalation: on a remediation attempt where the executor
    made changes but L1 still fails without improvement, an L2 probe is
    run. If the probe passes, ``l1_flagged`` is set but the node still
    remediates (L1 must pass for ``done``).

    Args:
        check_list: List of ``Check`` objects from the node.
        worktree: Absolute path to the node's git worktree.
        l2_fn: Optional L2 judge function.
        threshold: Minimum L2 score to pass.
        prev_l1_passed_ids: L1 passed_ids from the previous attempt (for delta).
        has_changes_since_prev: True if the executor made changes since last attempt.

    Returns:
        ``GateDecision`` with structured L1/L2 results.
    """
    from backend.evaluator.l1_checks import run_l1

    # --- L1: deterministic checks ---
    l1 = run_l1(check_list, worktree)
    l1_passed_ids = [cid for cid, ok, _ in l1.detail if ok]
    l1_feedback: list[dict[str, Any]] = []
    for cid, ok, output in l1.detail:
        if not ok:
            check = _find_check(check_list, cid)
            item = _build_l1_feedback_item(check, cid, output, worktree)
            l1_feedback.append(item)

    if not l1.passed:
        # False-fail escalation: on remediation attempt, if changes made
        # and no L1 improvement, run L2 probe to disambiguate.
        if prev_l1_passed_ids is not None and has_changes_since_prev:
            l1_improved = _l1_improved(l1_passed_ids, prev_l1_passed_ids)
            if not l1_improved and l2_fn is not None:
                l2_probe = l2_fn(check_list, worktree)
                if l2_probe.score >= threshold:
                    # L2 passes but L1 doesn't — suspected bad L1 check
                    return GateDecision(
                        action="remediate",
                        l1_passed_ids=l1_passed_ids,
                        l1_feedback=l1_feedback,
                        l1_flagged=True,
                        l2_passed=True,
                        goal_review=l2_probe.score,
                        l2_feedback=[_j_to_dict(j) for j in l2_probe.judgments],
                    )
        return GateDecision(
            action="remediate",
            l1_passed_ids=l1_passed_ids,
            l1_feedback=l1_feedback,
        )

    # --- L2: rubric judge (only if L1 passed) ---
    if l2_fn is not None:
        l2 = l2_fn(check_list, worktree)
        l2_passed = l2.score >= threshold
        l2_fb = [_j_to_dict(j) for j in l2.judgments]
        if not l2_passed:
            return GateDecision(
                action="remediate",
                l1_passed_ids=l1_passed_ids,
                l1_feedback=l1_feedback,
                l2_passed=False,
                goal_review=l2.score,
                l2_feedback=l2_fb,
            )
        return GateDecision(
            action="done",
            l1_passed_ids=l1_passed_ids,
            l1_feedback=l1_feedback,
            l2_passed=True,
            goal_review=l2.score,
            l2_feedback=l2_fb,
        )

    return GateDecision(
        action="done",
        l1_passed_ids=l1_passed_ids,
        l1_feedback=l1_feedback,
    )


# --- Helpers ---


def _find_check(check_list: list, cid: str) -> Any:
    """Find a check by id in the check list."""
    for c in check_list:
        if getattr(c, "id", None) == cid:
            return c
    return None


def _build_l1_feedback_item(
    check: Any, cid: str, output: str, worktree: str,
) -> dict[str, Any]:
    """Build L1 feedback dict with what/why/how/evidence."""
    on_fail = getattr(check, "on_fail", None) if check else None
    criterion = getattr(check, "criterion", "") if check else ""
    check_cmd = getattr(check, "check_cmd", "") if check else ""

    if on_fail:
        what = on_fail.what or f"L1 check failed: {cid}"
        how = on_fail.how or "Review the failing check and fix the issue"
        evidence_from = on_fail.evidence_from or "stdout"
        evidence = output[:500] if evidence_from == "stdout" else f"path check failed for {worktree}"
    else:
        what = getattr(check, "criterion", f"L1 check failed: {cid}") or f"L1 check failed: {cid}"
        how = "Review the failing check and fix the issue"
        evidence = output[:500]

    return {
        "check_id": cid,
        "tier": "L1",
        "what": what,
        "why": check_cmd or criterion,
        "how": how,
        "evidence": evidence,
        "criterion": criterion,
        "check_cmd": check_cmd,
        "worktree": worktree,
    }


def _j_to_dict(j: Any) -> dict[str, Any]:
    """Convert a Judgment object to dict."""
    if hasattr(j, "check_id"):
        return {
            "check_id": j.check_id,
            "criteria_met": j.criteria_met,
            "explanation": j.explanation,
            "what": f"L2 rubric: {j.check_id}",
            "why": j.explanation,
            "how": "Address the rubric item in the implementation",
            "evidence": j.explanation,
        }
    if isinstance(j, dict):
        return j
    return {"check_id": str(j), "what": "unknown"}


def _l1_improved(current: list[str], previous: list[str]) -> bool:
    """Check if L1 passed set grew without losing prior passes.

    Starter: compares by count. Upgrade to set-comparison when churn appears.
    """
    cur_set = set(current)
    prev_set = set(previous)
    # Must not lose any prior pass
    if not cur_set >= prev_set:
        return False
    # Must have gained at least one new pass
    return len(cur_set) > len(prev_set)
