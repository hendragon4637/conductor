"""GATE 17 — Test the plan brain module (model_selector, spec, brain).

Pass conditions:
1. spec.py Plan validation: acyclic, deps resolve, three-tier typed
2. spec.py validate_plan rejects invalid plans (cycle, unknown tool, empty success)
3. model_selector returns frontier config when BRAIN_PRIMARY set
4. model_selector falls back to local when BRAIN_PRIMARY unset
5. brain.propose_plan_v2 returns a spec-valid plan
6. budget_available returns bool
7. spec schema accepts hand-crafted valid plan
"""
from __future__ import annotations

import os
import sys
import json

sys.path.insert(0, "/opt/aipc/conductor")

from backend.planning.spec import (
    Plan as SpecPlan,
    PlanNode as SpecPlanNode,
    SuccessCriterion,
    validate_plan,
    _is_acyclic,
)
from backend.planning.model_selector import select_brain_model, budget_available
from backend.planning.brain import propose_plan_v2, _generate_plan_id

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
# 1. spec validation — valid plan
# ---------------------------------------------------------------------------
print("\n=== Test 1: Valid plan spec ===")
try:
    plan = SpecPlan(
        plan_id="test-1",
        user_intent="Build a finance tracker",
        worktree_decision={"project": "finance", "create_new_repo": True, "branch": "main"},
        nodes=[
            SpecPlanNode(
                id="node-1", kind="team", ref="orchestrator", role="orchestrator",
                project_id="finance",
                success=SuccessCriterion(
                    text="Architecture plan approved",
                    deterministic_checks=["pytest -q passes"],
                ),
            ),
            SpecPlanNode(
                id="node-2", kind="tool", ref="pytest", role="tester",
                project_id="finance",
                depends_on=["node-1"],
                success=SuccessCriterion(
                    text="All tests pass",
                    deterministic_checks=["pytest -q", "GET /health returns 200"],
                ),
            ),
        ],
    )
    validate_plan(plan, tool_registry={"pytest", "git"})
    check("valid plan passes validation", True)
    check("plan has 2 nodes", len(plan.nodes) == 2)
    check("DAG is acyclic", _is_acyclic(plan.nodes))
except Exception as e:
    print(f"  FAIL: exception {e}")
    FAIL += 1

# ---------------------------------------------------------------------------
# 2. spec validation — invalid plan (cycle)
# ---------------------------------------------------------------------------
print("\n=== Test 2: Reject invalid plans ===")
try:
    # Cycle
    SpecPlan(
        plan_id="cycle",
        user_intent="test",
        nodes=[
            SpecPlanNode(id="a", kind="tool", ref="pytest", project_id="x",
                         depends_on=["b"],
                         success=SuccessCriterion(text="ok")),
            SpecPlanNode(id="b", kind="tool", ref="git", project_id="x",
                         depends_on=["a"],
                         success=SuccessCriterion(text="ok")),
        ],
    )
    check("cycle plan rejected", False)  # should not reach here
except ValueError:
    check("cycle plan rejected", True)
except Exception as e:
    print(f"  FAIL: unexpected exception {e}")
    FAIL += 1

# Unknown dependency
try:
    SpecPlan(
        plan_id="unknown-dep",
        user_intent="test",
        nodes=[
            SpecPlanNode(id="a", kind="tool", ref="pytest", project_id="x",
                         depends_on=["nonexistent"],
                         success=SuccessCriterion(text="ok")),
        ],
    )
    check("unknown dep rejected", False)
except ValueError:
    check("unknown dep rejected", True)
except Exception as e:
    print(f"  FAIL: unexpected exception {e}")
    FAIL += 1

# Empty success text
try:
    validate_plan(SpecPlan(
        plan_id="empty-success",
        user_intent="test",
        nodes=[
            SpecPlanNode(id="a", kind="tool", ref="pytest", project_id="x",
                         success=SuccessCriterion(text="")),
        ],
    ))
    check("empty success rejected", False)
except ValueError:
    check("empty success rejected", True)
except Exception as e:
    print(f"  FAIL: unexpected exception {e}")
    FAIL += 1

# ---------------------------------------------------------------------------
# 3. model_selector — frontier when configured
# ---------------------------------------------------------------------------
print("\n=== Test 3: Model selector - frontier ===")
try:
    old_primary = os.environ.get("BRAIN_PRIMARY")
    old_fallback = os.environ.get("BRAIN_FALLBACK")
    os.environ["BRAIN_PRIMARY"] = "anthropic/claude-sonnet-4-20250514"
    os.environ["BRAIN_FALLBACK"] = "local-ovms/qwen3-8b-int4"

    cfg = select_brain_model("test")
    check("frontier selected when available", cfg["is_frontier"])
    check("provider is anthropic", cfg["provider"] == "anthropic")
    check("model is claude-sonnet", "claude" in cfg["model"])

    # Restore
    if old_primary:
        os.environ["BRAIN_PRIMARY"] = old_primary
    else:
        del os.environ["BRAIN_PRIMARY"]
    if old_fallback:
        os.environ["BRAIN_FALLBACK"] = old_fallback
except Exception as e:
    print(f"  FAIL: exception {e}")
    FAIL += 1

# ---------------------------------------------------------------------------
# 4. model_selector — fallback when no frontier
# ---------------------------------------------------------------------------
print("\n=== Test 4: Model selector - fallback ===")
try:
    old_primary = os.environ.pop("BRAIN_PRIMARY", None)

    cfg = select_brain_model("test")
    check("fallback selected when frontier unset", not cfg["is_frontier"])
    if old_primary is not None:
        os.environ["BRAIN_PRIMARY"] = old_primary
except Exception as e:
    print(f"  FAIL: exception {e}")
    FAIL += 1

# ---------------------------------------------------------------------------
# 5. budget_available
# ---------------------------------------------------------------------------
print("\n=== Test 5: Budget check ===")
try:
    old_budget = os.environ.get("BRAIN_BUDGET_TOKENS_DAY")
    if old_budget:
        del os.environ["BRAIN_BUDGET_TOKENS_DAY"]

    # No budget set → always available
    check("budget available when unset", budget_available())

    # Budget set with counter below limit
    os.environ["BRAIN_BUDGET_TOKENS_DAY"] = "1000000"
    check("budget available under limit", budget_available())

    if old_budget:
        os.environ["BRAIN_BUDGET_TOKENS_DAY"] = old_budget
    else:
        del os.environ["BRAIN_BUDGET_TOKENS_DAY"]
except Exception as e:
    print(f"  FAIL: exception {e}")
    FAIL += 1

# ---------------------------------------------------------------------------
# 6. spec schema - three-tier kinds present
# ---------------------------------------------------------------------------
print("\n=== Test 6: Three-tier kinds ===")
try:
    plan = SpecPlan(
        plan_id="three-tier",
        user_intent="test",
        nodes=[
            SpecPlanNode(id="t1", kind="tool", ref="pytest", project_id="x",
                         success=SuccessCriterion(text="pass")),
            SpecPlanNode(id="sa1", kind="single_agent", ref="summarizer", project_id="x",
                         depends_on=["t1"],
                         success=SuccessCriterion(text="summary")),
            SpecPlanNode(id="tm1", kind="team", ref="orchestrator", project_id="x",
                         depends_on=["sa1"],
                         success=SuccessCriterion(text="done")),
        ],
    )
    kinds = {n.kind for n in plan.nodes}
    check("team kind present", "team" in kinds)
    check("tool kind present", "tool" in kinds)
    check("single_agent kind present", "single_agent" in kinds)
    check("DAG is acyclic", _is_acyclic(plan.nodes))
except Exception as e:
    print(f"  FAIL: exception {e}")
    FAIL += 1

# ---------------------------------------------------------------------------
# 7. Tool ref must be in registry
# ---------------------------------------------------------------------------
print("\n=== Test 7: Tool ref validation ===")
try:
    plan = SpecPlan(
        plan_id="tool-reg",
        user_intent="test",
        nodes=[
            SpecPlanNode(id="bad", kind="tool", ref="nonexistent-tool",
                         project_id="x",
                         success=SuccessCriterion(text="ok")),
        ],
    )
    validate_plan(plan, tool_registry={"pytest", "git"})
    check("unknown tool rejected", False)
except ValueError:
    check("unknown tool rejected", True)

# ---------------------------------------------------------------------------
# 8. propose_plan_v2 — returns spec-valid plan (mock LLM)
# ---------------------------------------------------------------------------
print("\n=== Test 8: propose_plan_v2 with mock ===")
try:
    def mock_llm(prompt: str) -> str:
        return json.dumps({
            "nodes": [
                {
                    "id": "node-1",
                    "kind": "team",
                    "ref": "orchestrator",
                    "role": "orchestrator",
                    "project_id": "finance",
                    "depends_on": [],
                    "success": {
                        "text": "Architecture plan designed",
                        "deterministic_checks": ["plan approved"]
                    },
                },
                {
                    "id": "node-2",
                    "kind": "tool",
                    "ref": "pytest",
                    "role": "tester",
                    "project_id": "finance",
                    "depends_on": ["node-1"],
                    "success": {
                        "text": "All tests pass",
                        "deterministic_checks": ["pytest -q"]
                    },
                },
            ],
            "worktree_decision": {
                "project": "finance",
                "create_new_repo": False,
                "branch": "main",
            },
            "plan_id": "plan-test-mock",
        })

    plan = propose_plan_v2(
        "Build a finance tracker",
        context={"project": "finance"},
        available_tools={"pytest", "git"},
        llm_call=mock_llm,
    )
    check("propose_plan_v2 returns SpecPlan", isinstance(plan, SpecPlan))
    check("plan has nodes", len(plan.nodes) >= 2)
    kinds = {n.kind for n in plan.nodes}
    check("three-tier kinds present", "team" in kinds)
    check("at least one tool/single_agent", bool(kinds & {"tool", "single_agent"}))

    # Should already be validated by propose_plan_v2
    validate_plan(plan, tool_registry={"pytest", "git"})
    check("plan passes validate_plan", True)
except Exception as e:
    print(f"  FAIL: exception {e}")
    FAIL += 1

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print(f"\n{'='*50}")
print(f"GATE 17 RESULTS: {PASS} passed, {FAIL} failed out of {PASS+FAIL}")
print(f"{'='*50}")

if FAIL > 0:
    sys.exit(1)
else:
    print("GATE 17: PASS")
