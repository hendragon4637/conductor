"""Evaluator gate — decides whether a node should advance or remediate.

L1 (deterministic) → L2 (rubric judge, if provided).
L2 score is stored in the returned GateDecision for OLTP write.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class GateDecision:
    """Decision from the evaluator gate."""
    action: str  # "advance" | "remediate"
    reason: dict = field(default_factory=dict)
    goal_review: float | None = None  # L2 score, set when l2_fn runs


def evaluate_gate(
    check_list: list,
    worktree: str,
    l2_fn=None,
    threshold: float = 0.7,
) -> GateDecision:
    """Run evaluator gates for a node.

    Order: L1 (deterministic) → L2 (rubric judge, if provided).
    If L2 runs, ``goal_review`` is set on the returned decision.

    Args:
        check_list: List of ``Check`` objects from the node.
        worktree: Absolute path to the node's git worktree (for L1 shell commands).
        l2_fn: Optional L2 judge function (signature: ``(checks, worktree) -> L2Result``).
        threshold: Minimum L2 score to pass.

    Returns:
        ``GateDecision`` with action, detail, and optional goal_review.
    """
    from backend.evaluator.l1_checks import run_l1

    l1 = run_l1(check_list, worktree)
    if not l1.passed:
        return GateDecision(
            action="remediate",
            reason={"layer": "L1", "detail": l1.detail},
        )

    if l2_fn is not None:
        l2 = l2_fn(check_list, worktree)
        if l2.score < threshold:
            return GateDecision(
                action="remediate",
                reason={"layer": "L2", "detail": l2.judgments},
                goal_review=l2.score,
            )
        return GateDecision(
            action="advance",
            reason={"L1_detail": l1.detail, "L2_score": l2.score},
            goal_review=l2.score,
        )

    return GateDecision(
        action="advance",
        reason={"L1_detail": l1.detail},
    )
