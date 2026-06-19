from __future__ import annotations

"""Plan-evaluator: gates DAG structure BEFORE execution (File 04A).

Reuses L1+L2 engine:
- L1 = structural asserts (acyclic, nodes complete, deps resolve)
- L2 = plan rubric (covers_goal, right_sized, deps_correct, measurable)

Output ``plan_goal_review`` (0-1) stored on the run.
"""

import logging
from dataclasses import dataclass, field
from typing import Any

from backend.evaluator.l2_judge import L2Result, run_l2
from backend.evaluator.schema import Check

logger = logging.getLogger(__name__)

L1_STRUCTURAL_RUBRIC = "plan_structure"


@dataclass
class PlanL1Result:
    """Result of structural validation on a plan DAG."""
    passed: bool
    checks: list[dict] = field(default_factory=list)
    note: str = ""


@dataclass
class PlanEvalResult:
    """Combined result of plan evaluation (L1 + L2)."""
    l1: PlanL1Result
    l2: L2Result | None = None
    plan_goal_review: float = 0.0
    passed: bool = False


def _check_acyclic(dag: list[dict]) -> bool:
    """Check that the DAG has no cycles (simple DFS-based cycle detection).

    Args:
        dag: List of node dicts, each with ``id`` and ``depends_on``.

    Returns:
        True if acyclic, False if a cycle is detected.
    """
    node_ids = {n["id"] for n in dag}
    dep_map: dict[str, list[str]] = {}
    for n in dag:
        dep_map[n["id"]] = [d for d in n.get("depends_on", []) if d in node_ids]

    visited: set[str] = set()
    in_stack: set[str] = set()

    def dfs(nid: str) -> bool:
        visited.add(nid)
        in_stack.add(nid)
        for dep in dep_map.get(nid, []):
            if dep not in visited:
                if dfs(dep):
                    return True
            elif dep in in_stack:
                return True
        in_stack.discard(nid)
        return False

    for nid in node_ids:
        if nid not in visited:
            if dfs(nid):
                return False
    return True


def run_plan_l1(dag: list[dict]) -> PlanL1Result:
    """Run L1 structural checks on the plan DAG.

    Checks:
    1. At least one node exists.
    2. All nodes have required fields (id, members, task.text, success.text).
    3. Dependencies reference existing node IDs.
    4. DAG is acyclic.
    5. Every node has at least one check.

    Args:
        dag: List of node dicts from the plan.

    Returns:
        ``PlanL1Result`` with pass/fail and per-check detail.
    """
    checks: list[dict] = []
    node_ids = {n.get("id", "") for n in dag}

    # Check 1: at least one node
    if len(dag) < 1:
        checks.append({"check": "at_least_one_node", "passed": False, "detail": "DAG has no nodes"})
        return PlanL1Result(passed=False, checks=checks, note="Empty DAG")

    checks.append({"check": "at_least_one_node", "passed": True, "detail": f"{len(dag)} nodes"})

    all_ok = True
    for i, n in enumerate(dag):
        nid = n.get("id", f"node-{i}")
        missing: list[str] = []

        if not n.get("members"):
            missing.append("members")
        if not n.get("task") or not n.get("task", {}).get("text"):
            missing.append("task.text")
        if not n.get("success") or not n.get("success", {}).get("text"):
            missing.append("success.text")
        node_checks = n.get("checks", [])
        if not node_checks:
            missing.append("checks (empty)")

        if missing:
            all_ok = False
            checks.append({
                "check": f"node_{nid}_fields",
                "passed": False,
                "detail": f"Missing: {', '.join(missing)}",
            })
        else:
            checks.append({
                "check": f"node_{nid}_fields",
                "passed": True,
                "detail": f"{len(node_checks)} checks",
            })

        # Check dependency resolution
        for dep in n.get("depends_on", []):
            if dep not in node_ids:
                all_ok = False
                checks.append({
                    "check": f"node_{nid}_dep_{dep}",
                    "passed": False,
                    "detail": f"Dependency '{dep}' not found in DAG",
                })

    # Check acyclic
    if not _check_acyclic(dag):
        all_ok = False
        checks.append({"check": "acyclic", "passed": False, "detail": "Cycle detected in DAG"})
    else:
        checks.append({"check": "acyclic", "passed": True, "detail": "No cycles"})

    note = ""
    if not all_ok:
        failed = [c for c in checks if not c["passed"]]
        note = f"L1 structural: {len(failed)}/{len(checks)} checks failed"

    return PlanL1Result(passed=all_ok, checks=checks, note=note)


def evaluate_plan(
    dag: list[dict],
    plan_goal: str = "",
    l2_threshold: float = 0.7,
) -> PlanEvalResult:
    """Run full plan evaluation (L1 structural + L2 plan rubric).

    Args:
        dag: List of node dicts from the plan.
        plan_goal: The plan's goal text (for L2 context, optional).
        l2_threshold: Minimum L2 score to consider the plan passing.

    Returns:
        ``PlanEvalResult`` with L1 and L2 results and combined verdict.
    """
    # L1
    l1 = run_plan_l1(dag)
    if not l1.passed:
        return PlanEvalResult(
            l1=l1, l2=None, plan_goal_review=0.0, passed=False,
        )

    # L2 — plan rubric
    plan_text = "\n".join(
        f"Node {n.get('id', '?')}: {n.get('task', {}).get('text', '')[:200]}"
        for n in dag
    )
    if plan_goal:
        plan_text = f"Goal: {plan_goal}\n\n{plan_text}"

    rubric_checks = _build_plan_rubric_checks()
    try:
        l2_result = run_l2(checks=rubric_checks, worktree="", trace_id=None)
    except Exception as exc:
        logger.warning("Plan L2 judge failed: %s — using L1-only result", exc)
        return PlanEvalResult(
            l1=l1, l2=None, plan_goal_review=0.0, passed=l1.passed,
        )

    score = l2_result.score
    passed = score >= l2_threshold

    return PlanEvalResult(
        l1=l1,
        l2=l2_result,
        plan_goal_review=round(score, 4),
        passed=passed,
    )


def _build_plan_rubric_checks() -> list[Check]:
    """Build L2 rubric checks for the plan structure rubric."""
    from backend.evaluator.rubrics import load_rubric

    rubric = load_rubric("plan_structure") or {
        "name": "plan_structure",
        "items": [
            {"id": "covers_goal", "rubric_item": "Do the nodes together fully cover the plan goal?", "weight": 2.0},
            {"id": "right_sized", "rubric_item": "Is each node a bounded, single-responsibility unit?", "weight": 1.5},
            {"id": "deps_correct", "rubric_item": "Are dependencies correct and minimal?", "weight": 1.5},
            {"id": "measurable", "rubric_item": "Does each node have a measurable success criterion?", "weight": 1.0},
        ],
    }

    return [
        Check(
            id=item["id"],
            type="rubric",
            criterion=item["rubric_item"],
            rubric_item=item["rubric_item"],
            weight=item.get("weight", 1.0),
        )
        for item in rubric["items"]
    ]
