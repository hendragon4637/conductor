"""Evaluator gate — decides whether a node should advance or remediate.

L1 (deterministic) -> L2 (rubric judge).
Strict both-pass: ``done`` requires L1 AND L2 to pass.
False-fail escalation: if L1 fails on a remediation attempt but the executor
made changes and L1 did not improve, an L2 probe is run to disambiguate.
"""
from __future__ import annotations

import logging
import subprocess

from dataclasses import dataclass, field
from typing import Any, Callable

from contracts.paths import READ_ONLY_CONTEXT_PATHS

logger = logging.getLogger(__name__)


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
    action: str  # "done" | "remediate" | "requeue"
    l1_passed_ids: list[str] = field(default_factory=list)
    l1_feedback: list[dict[str, Any]] = field(default_factory=list)
    l1_flagged: bool = False
    l2_passed: bool = False
    goal_review: float | None = None
    l2_feedback: list[dict[str, Any]] = field(default_factory=list)
    l2_chunk_idx: int = 0
    """Chunk index from the last partial L2 result (set only when action='requeue')."""


_NO_CHANGE_FEEDBACK: list[dict[str, Any]] = [
    {
        "check_id": "_no_changes",
        "tier": "L2",
        "what": "Node execution produced no changes or deliverables",
        "why": (
            "verdict=done_no_change with no git diff and prev_l1_passed_ids is set "
            "— the agent made no modifications to the worktree"
        ),
        "how": "The agent must produce tangible output — code, files, or modifications",
    },
]


def _is_worktree_diff_empty(worktree: str) -> bool:
    try:
        r1 = subprocess.run(
            ["git", "diff", "--quiet"],
            cwd=worktree,
            capture_output=True,
            timeout=15,
        )
        if r1.returncode != 0:
            return False
        r2 = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=worktree,
            capture_output=True,
            text=True,
            timeout=15,
        )
        return r2.stdout.strip() == ""
    except Exception:
        return False


def evaluate_gate(
    check_list: list,
    worktree: str,
    l2_fn: Callable | None = None,
    threshold: float = 0.7,
    prev_l1_passed_ids: list[str] | None = None,
    has_changes_since_prev: bool = False,
    verdict: str | None = None,
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

    print(f"[GATE] evaluate_gate called: worktree={worktree} threshold={threshold} prev_l1_ids={prev_l1_passed_ids} has_changes={has_changes_since_prev}", flush=True)

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
        # L2-gating on remediation: when changes exist, run L2 even if L1
        # fails. This allows the patience system to track L2 improvement
        # and avoid failing when substantive work is being done but L1
        # checks are mis-specified or the worktree needs an L1 pass.
        if prev_l1_passed_ids is not None and has_changes_since_prev and l2_fn is not None:
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
            # L2 also fails — include L2 score for patience tracking
            return GateDecision(
                action="remediate",
                l1_passed_ids=l1_passed_ids,
                l1_feedback=l1_feedback,
                l2_passed=False,
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
        # Gap 4: Empty-diff short-circuit — skip L2 when node produced no changes
        if prev_l1_passed_ids is not None and verdict == "done_no_change" and _is_worktree_diff_empty(worktree):
            print(f"[GATE] Empty-diff short-circuit: worktree={worktree} prev_ids={prev_l1_passed_ids}", flush=True)
            return GateDecision(
                action="remediate",
                l1_passed_ids=l1_passed_ids,
                l1_feedback=l1_feedback,
                l2_passed=False,
                goal_review=0.0,
                l2_feedback=_NO_CHANGE_FEEDBACK,
            )

        print(f"[GATE] L1 passed, calling L2 judge: worktree={worktree} threshold={threshold}", flush=True)
        l2 = l2_fn(check_list, worktree)
        if getattr(l2, "partial", False):
            print(f"[GATE] L2 partial result — {len(l2.judgments)} items completed, requeuing", flush=True)
            l2_fb = [_j_to_dict(j) for j in l2.judgments]
            return GateDecision(
                action="requeue",
                l2_passed=False,
                goal_review=0.0,
                l2_feedback=l2_fb,
                l2_chunk_idx=l2.best_chunk_idx,
            )

        l2_passed = l2.score >= threshold
        print(f"[GATE] L2 result: score={l2.score:.4f} threshold={threshold} passed={l2_passed} items_met={l2.items_met}/{l2.rubric_count} oversize={l2.oversize}", flush=True)
        l2_fb = [_j_to_dict(j) for j in l2.judgments]

        # Filter out read-only context path feedback (deps/, references)
        filtered: list[dict[str, Any]] = []
        for fb in l2_fb:
            where = fb.get("where", "")
            what = fb.get("what", "")
            if any(p in where or p in what for p in READ_ONLY_CONTEXT_PATHS):
                logger.warning(
                    "Rejecting L2 feedback referencing read-only context paths: check_id=%s where=%s",
                    fb.get("check_id", "?"), where,
                )
                continue
            filtered.append(fb)
        l2_fb = filtered

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
    check_cmd = (getattr(check, "cmd", None) or getattr(check, "check_cmd", None) or "") if check else ""

    if on_fail:
        what = on_fail.what or f"L1 check failed: {cid}"
        how = on_fail.how or "Review the failing check and fix the issue"
        evidence_from = on_fail.evidence_from or "stdout"
        evidence = output[:500] if evidence_from == "stdout" else f"path check failed for {worktree}"
    else:
        criterion_text = getattr(check, "criterion", "") or ""
        what = criterion_text or check_cmd or f"L1 check failed: {cid}"
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
    """Convert a Judgment object to dict, preserving structured feedback."""
    if hasattr(j, "check_id"):
        fb = getattr(j, "feedback_raw", None) or {}
        what = fb.get("what") or f"L2 rubric: {j.check_id}"
        where = fb.get("where", "unspecified")
        why = fb.get("why") or j.explanation
        how = fb.get("how") or "Address the rubric item in the implementation"
        return {
            "check_id": j.check_id,
            "criteria_met": j.criteria_met,
            "explanation": j.explanation,
            "what": what,
            "where": where,
            "why": why,
            "how": how,
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
