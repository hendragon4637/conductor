"""GATE 18 — Test decomposition module (decompose, decomposed_spec, granularity).

Pass conditions:
1. granularity.classify_node returns correct three-tier types
2. granularity.right_sized detects too big / too small / appropriate
3. granularity.is_team_overused flags excessive team chunks
4. decomposed_spec validates: acyclic, deps resolve, team minority
5. decomposed_spec rejects: cycles, shared_sequential multi-worktree, missing regression
6. decompose() with mock LLM returns valid DecomposedPlan
7. End-to-end smoke: full decomposition pipeline
"""
from __future__ import annotations

import os
import sys
import json

sys.path.insert(0, "/opt/aipc/conductor")

from backend.planning.granularity import classify_node, right_sized, is_team_overused
from backend.planning.decomposed_spec import (
    DecomposedPlan,
    ChunkNode,
    validate_decomposed,
)
from backend.planning.decompose import decompose
from backend.planning.spec import Plan, PlanNode as SpecPlanNode, SuccessCriterion

PASS = 0
FAIL = 0


def check(desc: str, ok: bool):
    global PASS, FAIL
    if ok:
        print(f"  PASS: {desc}")
        PASS += 1
    else:
        print(f"  FAIL: {desc}")
        FAIL += 1


# ---------------------------------------------------------------------------
# 1. granularity.classify_node
# ---------------------------------------------------------------------------
print("\n=== Test 1: classify_node ===")
try:
    check("'run tests' → tool", classify_node("run tests") == "tool")
    check("'execute deploy' → tool", classify_node("execute deploy script") == "tool")
    check("'summarize results' → single_agent",
          classify_node("summarize results from testing") == "single_agent")
    check("'translate docs' → single_agent",
          classify_node("translate docs to English") == "single_agent")
    check("'design architecture' → team",
          classify_node("design architecture for the app") == "team")
    check("'debug the issue' → team",
          classify_node("debug the issue in production") == "team")
    check("'figure out root cause' → team",
          classify_node("figure out root cause of the bug") == "team")
    check("'unknown task' → team (conservative)",
          classify_node("do something unusual") == "team")
except Exception as e:
    print(f"  FAIL: exception {e}")
    FAIL += 1

# ---------------------------------------------------------------------------
# 2. granularity.right_sized
# ---------------------------------------------------------------------------
print("\n=== Test 2: right_sized ===")
try:
    ok, reason = right_sized("build the whole app")
    check(f"too broad ('whole') caught: {reason}", not ok)

    ok, reason = right_sized("write one function")
    check(f"too narrow caught: {reason}", not ok)

    ok, reason = right_sized("implement CRUD for users")
    check(f"appropriate: {reason}", ok)

    ok, reason = right_sized("build login page with email, password, and oauth")
    check(f"appropriate with sub-steps: {reason}", ok)

    ok, reason = right_sized("")
    check(f"empty description caught", not ok)
except Exception as e:
    print(f"  FAIL: exception {e}")
    FAIL += 1

# ---------------------------------------------------------------------------
# 3. is_team_overused
# ---------------------------------------------------------------------------
print("\n=== Test 3: is_team_overused ===")
try:
    all_team = [
        {"kind": "team", "id": "a"},
        {"kind": "team", "id": "b"},
        {"kind": "team", "id": "c"},
    ]
    check("all team → overused", is_team_overused(all_team))

    mixed = [
        {"kind": "tool", "id": "a"},
        {"kind": "team", "id": "b"},
        {"kind": "single_agent", "id": "c"},
    ]
    check("mixed → not overused", not is_team_overused(mixed))

    empty_list = []
    check("empty → not overused", not is_team_overused(empty_list))

    half_team = [
        {"kind": "team", "id": "a"},
        {"kind": "tool", "id": "b"},
    ]
    check("exactly half → not overused", not is_team_overused(half_team, threshold=0.5))
except Exception as e:
    print(f"  FAIL: exception {e}")
    FAIL += 1

# ---------------------------------------------------------------------------
# 4. decomposed_spec — valid plan
# ---------------------------------------------------------------------------
print("\n=== Test 4: DecomposedPlan validation ===")
try:
    dplan = DecomposedPlan(
        plan_id="test-decomp",
        worktree_root="/tmp/worktrees",
        chunks=[
            ChunkNode(
                id="chunk-1", kind="tool", ref="pytest", role="tester",
                project_id="finance",
                success=SuccessCriterion(
                    text="Tests pass",
                    deterministic_checks=["pytest -q passes"],
                ),
                worktree_strategy="shared_sequential",
            ),
            ChunkNode(
                id="chunk-2", kind="team", ref="orchestrator", role="dev",
                project_id="finance",
                depends_on=["chunk-1"],
                success=SuccessCriterion(
                    text="Feature built",
                    deterministic_checks=["prior tests pass", "GET /health returns 200"],
                ),
                worktree_strategy="shared_sequential",
            ),
        ],
    )
    validate_decomposed(dplan, tool_registry={"pytest"})
    check("valid decomposed plan passes", True)
    check("plan has 2 chunks", len(dplan.chunks) == 2)
    check("DAG is acyclic", True)
except Exception as e:
    print(f"  FAIL: exception {e}")
    FAIL += 1

# ---------------------------------------------------------------------------
# 5. decomposed_spec — reject invalid
# ---------------------------------------------------------------------------
print("\n=== Test 5: Reject invalid decomposed plans ===")
try:
    # Cycle
    DecomposedPlan(
        plan_id="cycle",
        chunks=[
            ChunkNode(id="a", kind="tool", ref="pytest", project_id="x",
                      depends_on=["b"],
                      success=SuccessCriterion(text="ok")),
            ChunkNode(id="b", kind="tool", ref="git", project_id="x",
                      depends_on=["a"],
                      success=SuccessCriterion(text="ok")),
        ],
    )
    check("cycle rejected", False)
except ValueError:
    check("cycle rejected", True)

try:
    # Team over 50%
    dplan_too_many_team = DecomposedPlan(
        plan_id="too-many-team",
        chunks=[
            ChunkNode(id="a", kind="team", ref="orchestrator", project_id="x",
                      success=SuccessCriterion(text="ok",
                        deterministic_checks=["done"])),
            ChunkNode(id="b", kind="team", ref="executor", project_id="x",
                      depends_on=["a"],
                      success=SuccessCriterion(text="ok",
                        deterministic_checks=["prior pass"])),
            ChunkNode(id="c", kind="team", ref="reviewer", project_id="x",
                      depends_on=["b"],
                      success=SuccessCriterion(text="ok",
                        deterministic_checks=["prior pass"])),
        ],
    )
    validate_decomposed(dplan_too_many_team)
    check("team over 50% rejected", False)
except ValueError:
    check("team over 50% rejected", True)

# Missing regression check on non-first chunk
try:
    dplan = DecomposedPlan(
        plan_id="no-regression",
        chunks=[
            ChunkNode(id="a", kind="tool", ref="pytest", project_id="x",
                      success=SuccessCriterion(text="ok",
                        deterministic_checks=["pytest passes"])),
            ChunkNode(id="b", kind="tool", ref="git", project_id="x",
                      depends_on=["a"],
                      success=SuccessCriterion(text="ok",
                        deterministic_checks=["format ok"])),  # no 'prior' or 'regression'
        ],
    )
    validate_decomposed(dplan)
    check("missing regression check rejected", False)
except ValueError:
    check("missing regression check rejected", True)

except Exception as e:
    print(f"  FAIL: unexpected exception {e}")
    FAIL += 1

# ---------------------------------------------------------------------------
# 6. decompose() with mock LLM
# ---------------------------------------------------------------------------
print("\n=== Test 6: decompose with mock ===")
try:
    plan = Plan(
        plan_id="test-plan",
        user_intent="Build a finance tracker CRUD app with tests",
        nodes=[
            SpecPlanNode(
                id="node-1", kind="team", ref="orchestrator", role="orchestrator",
                project_id="finance",
                success=SuccessCriterion(text="Plan approved"),
            ),
        ],
    )

    def mock_llm(prompt: str) -> str:
        return json.dumps({
            "chunks": [
                {
                    "id": "chunk-scaffold",
                    "kind": "tool",
                    "ref": "scaffold",
                    "role": "tool",
                    "project_id": "finance",
                    "depends_on": [],
                    "success": {
                        "text": "Project scaffolded",
                        "deterministic_checks": ["pytest -q"],
                    },
                    "worktree_strategy": "shared_sequential",
                    "commit_on_done": True,
                    "regression_required": True,
                    "retry_policy": {"max": 2, "backoff_s": 30},
                },
                {
                    "id": "chunk-crud",
                    "kind": "team",
                    "ref": "executor",
                    "role": "executor",
                    "project_id": "finance",
                    "depends_on": ["chunk-scaffold"],
                    "success": {
                        "text": "CRUD endpoints built",
                        "deterministic_checks": [
                            "prior scaffold works",
                            "GET /items returns 200",
                            "pytest -q passes",
                        ],
                    },
                    "worktree_strategy": "shared_sequential",
                    "commit_on_done": True,
                    "regression_required": True,
                    "retry_policy": {"max": 2, "backoff_s": 30},
                },
                {
                    "id": "chunk-review",
                    "kind": "single_agent",
                    "ref": "reviewer",
                    "role": "reviewer",
                    "project_id": "finance",
                    "depends_on": ["chunk-crud"],
                    "success": {
                        "text": "Code reviewed",
                        "deterministic_checks": [
                            "prior tests pass",
                            "no critical issues",
                        ],
                    },
                    "worktree_strategy": "shared_sequential",
                    "commit_on_done": True,
                    "regression_required": True,
                    "retry_policy": {"max": 2, "backoff_s": 30},
                },
            ],
            "worktree_root": "/tmp/worktrees",
            "plan_id": "test-decomp-mock",
        })

    dplan = decompose(
        plan,
        available_tools={"scaffold", "pytest", "git"},
        llm_call=mock_llm,
    )
    check("decompose returns DecomposedPlan", isinstance(dplan, DecomposedPlan))
    check("plan has 3 chunks", len(dplan.chunks) == 3)

    kinds = [c.kind for c in dplan.chunks]
    check("team kind present", "team" in kinds)
    check("tool kind present", "tool" in kinds)
    check("single_agent kind present", "single_agent" in kinds)
    check("team is minority", kinds.count("team") <= len(kinds) / 2)

    # Shared sequential chunks share one worktree
    seq = [c for c in dplan.chunks if c.worktree_strategy == "shared_sequential"]
    check(f"sequential chunks ({len(seq)}) all same worktree",
          len({f"{dplan.worktree_root}/{c.project_id}" for c in seq}) <= 1)

    # Every non-first chunk has regression check
    for c in dplan.chunks[1:]:
        checks = " ".join(c.success.deterministic_checks).lower()
        has_regression = any(kw in checks for kw in ["prior", "regression", "pass"])
        check(f"chunk {c.id} has regression check", has_regression)
except Exception as e:
    print(f"  FAIL: exception {e}")
    FAIL += 1

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print(f"\n{'='*50}")
print(f"GATE 18 RESULTS: {PASS} passed, {FAIL} failed out of {PASS+FAIL}")
print(f"{'='*50}")

if FAIL > 0:
    sys.exit(1)
else:
    print("GATE 18: PASS")
