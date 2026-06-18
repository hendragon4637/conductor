"""File 07 — Plan Brain + Plan DAG"""
import json
import os

from dotenv import load_dotenv
import pytest

load_dotenv("/opt/aipc/conductor/.env")

from backend.planning import propose_plan, save_plan, set_status, get_plan
from backend.planning.schema import Plan, PlanNode

TEST_PROJECT = "e2e-input-test"
TEST_SESSION = "feat/e2e-input"


def _mock_llm_3node(prompt: str) -> str:
    """Mock brain LLM returning a 3-node DAG."""
    return json.dumps({
        "nodes": [
            {
                "id": "node-1",
                "agent_config": "opencode:backend-planner",
                "role": "planner",
                "depends_on": [],
                "success": "A clear implementation plan is written",
                "project_id": TEST_PROJECT,
            },
            {
                "id": "node-2",
                "agent_config": "opencode:backend-executor",
                "role": "executor",
                "depends_on": ["node-1"],
                "success": "Endpoint /auth/refresh is implemented with tests",
                "project_id": TEST_PROJECT,
            },
            {
                "id": "node-3",
                "agent_config": "opencode:backend-reviewer",
                "role": "reviewer",
                "depends_on": ["node-2"],
                "success": "All tests pass and code review passes",
                "project_id": TEST_PROJECT,
            },
        ]
    })


def test_propose_plan_with_mock():
    cfgs = [
        {"agent_config_id": "opencode:backend-planner", "role": "planner"},
        {"agent_config_id": "opencode:backend-executor", "role": "executor"},
        {"agent_config_id": "opencode:backend-reviewer", "role": "reviewer"},
    ]
    plan = propose_plan(
        "Add /auth/refresh endpoint with tests",
        context={"project": TEST_PROJECT},
        available_agent_configs=cfgs,
        llm_call=_mock_llm_3node,
    )

    assert isinstance(plan, Plan)
    assert len(plan.nodes) >= 2
    assert all(n.agent_config in {c["agent_config_id"] for c in cfgs} for n in plan.nodes)

    # Dependency sanity: executor depends on planner, reviewer on executor
    node_ids = {n.id for n in plan.nodes}
    for n in plan.nodes:
        for dep in n.depends_on:
            assert dep in node_ids, f"node {n.id} depends on {dep} which doesn't exist"

    # Set session and persist
    plan.session_id = TEST_SESSION
    save_plan(plan)
    set_status(plan.plan_id, "approved")

    # Verify persistence
    loaded = get_plan(plan.plan_id)
    assert loaded is not None
    assert loaded["approval_status"] == "approved"
    assert len(loaded["dag"]) == 3

    # Clean up test plan
    import psycopg
    db_url = os.environ.get("DATABASE_URL", "")
    if db_url:
        with psycopg.connect(db_url) as c:
            with c.cursor() as cur:
                cur.execute("DELETE FROM plans WHERE plan_id = %s", (plan.plan_id,))
            c.commit()


@pytest.mark.slow
def test_propose_plan_real_llm():
    """Integration test — requires the local brain LLM on port 11434."""
    cfgs = [
        {"agent_config_id": "opencode:backend-planner", "role": "planner"},
        {"agent_config_id": "opencode:backend-executor", "role": "executor"},
        {"agent_config_id": "opencode:backend-reviewer", "role": "reviewer"},
    ]
    plan = propose_plan(
        "Add a health-check endpoint returning JSON status",
        context={"project": "backend-api"},
        available_agent_configs=cfgs,
    )

    assert isinstance(plan, Plan)
    assert len(plan.nodes) >= 2
    print(f"\nReal LLM plan: {plan.model_dump_json(indent=2)}")
