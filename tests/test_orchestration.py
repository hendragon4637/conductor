"""File 08 — Spawn Orchestration end-to-end test.

Requires: AionUi running, Postgres, Langfuse.
"""
import asyncio
import json
import os

from dotenv import load_dotenv
import psycopg
import pytest

load_dotenv("/opt/aipc/conductor/.env")

from backend.aionui import AionUiClient
from backend.orchestration import run_plan
from backend.orchestration.spawn import spawn_node
from backend.worktree import WorktreeManager

DB_URL = os.environ.get("DATABASE_URL", "")
AIONUI_HOST = os.environ.get("AIONUI_HOST", "http://127.0.0.1:40937")
WORKSPACE_ROOT = os.environ.get("WORKSPACE_ROOT", "/opt/aipc/conductor/workspace")

TEST_PROJECT = "e2e-input-test"
_UNIQUE = str(int(__import__("time").time()))[-6:]
TEST_SESSION = f"orch/{_UNIQUE}"


@pytest.fixture(autouse=True)
def ensure_plan_and_session():
    """Create test session and clean up plans from prior runs."""
    with psycopg.connect(DB_URL) as c:
        with c.cursor() as cur:
            # Create unique session for this test run
            cur.execute(
                """INSERT INTO sessions
                   (session_id, project_id, user_intent, status, base_branch)
                   VALUES (%s, %s, 'test-orchestration', 'active', 'main')
                   ON CONFLICT (project_id, session_id) DO NOTHING
                """,
                (TEST_SESSION, TEST_PROJECT),
            )
            # Clean old test plans and referencing rows
            cur.execute(
                "DELETE FROM aionui_links WHERE task_id IN "
                "(SELECT task_id FROM tasks WHERE plan_id LIKE 'test-orch-%')"
            )
            cur.execute(
                "DELETE FROM tasks WHERE plan_id LIKE 'test-orch-%'"
            )
            cur.execute(
                "DELETE FROM plans WHERE plan_id LIKE 'test-orch-%'"
            )
        c.commit()
    yield
    # Clean up test worktree if created
    import shutil
    wt_dir = f"{WORKSPACE_ROOT}/{TEST_PROJECT}.{TEST_SESSION.replace('/', '-')}"
    wt_path = __import__("pathlib").Path(wt_dir)
    if wt_path.exists():
        try:
            wm = WorktreeManager(WORKSPACE_ROOT)
            wm.remove(TEST_PROJECT, str(wt_path))
        except Exception:
            shutil.rmtree(wt_path, ignore_errors=True)


def test_spawn_single_node():
    """Spawn a single executor node into AionUi and verify it works."""
    plan = {
        "plan_id": "test-orch-single",
        "user_intent": "list files in the current directory and stop",
        "dag": [
            {
                "id": "node-exec",
                "agent_config": "opencode:backend-executor",
                "role": "executor",
                "depends_on": [],
                "success": "Files are listed in the workspace",
                "project_id": TEST_PROJECT,
            }
        ],
    }

    aionui = AionUiClient(AIONUI_HOST)
    wm = WorktreeManager(WORKSPACE_ROOT)

    conv_id = spawn_node(
        node=plan["dag"][0],
        plan=plan,
        session_id=TEST_SESSION,
        aionui=aionui,
        wm=wm,
        db_url=DB_URL,
        workspace_root=WORKSPACE_ROOT,
    )

    assert conv_id is not None
    assert isinstance(conv_id, str) and len(conv_id) > 0

    # Wait briefly for agent to process
    import time
    time.sleep(10)

    conv = aionui.get_conversation(conv_id)
    print(f"Conversation status: {conv.get('status')}")

    # Verify a task was created in the DB
    with psycopg.connect(DB_URL) as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT task_id, status FROM tasks WHERE plan_id = %s",
                (plan["plan_id"],),
            )
            rows = cur.fetchall()
            assert len(rows) >= 1, "No task created for the plan node"
            print(f"Task(s) created: {rows}")


@pytest.mark.slow
def test_run_single_node_plan():
    """Full DAG execution: run_plan for a single-node plan."""
    plan = {
        "plan_id": "test-orch-full",
        "user_intent": "list files in the workspace and stop",
        "dag": [
            {
                "id": "node-1",
                "agent_config": "opencode:backend-executor",
                "role": "executor",
                "depends_on": [],
                "success": "Files listed",
                "project_id": TEST_PROJECT,
            }
        ],
    }

    # Insert the plan into DB so runner can set_status
    _insert_plan(plan)

    results = asyncio.run(
        run_plan(
            plan=plan,
            session_id=TEST_SESSION,
            db_url=DB_URL,
            aionui_host=AIONUI_HOST,
            workspace_root=WORKSPACE_ROOT,
        )
    )

    assert len(results) == 1
    assert "node-1" in results
    trace_id = results["node-1"]
    assert isinstance(trace_id, str) and len(trace_id) > 0
    print(f"Langfuse trace: {trace_id}")

    # Verify plan went live
    with psycopg.connect(DB_URL) as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT approval_status FROM plans WHERE plan_id = %s",
                (plan["plan_id"],),
            )
            row = cur.fetchone()
            assert row is not None
            print(f"Plan status: {row[0]}")

    # Verify aionui_links has langfuse_trace_id
    with psycopg.connect(DB_URL) as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT langfuse_trace_id, status FROM aionui_links "
                "WHERE aionui_conversation_id IN "
                "(SELECT link_id FROM aionui_links WHERE task_id IN "
                "(SELECT task_id FROM tasks WHERE plan_id = %s))",
                (plan["plan_id"],),
            )
            rows = cur.fetchall()
            if rows:
                print(f"AionUi links: {rows}")


def _insert_plan(plan: dict) -> None:
    """Insert a plan into the DB for testing."""
    with psycopg.connect(DB_URL) as c:
        with c.cursor() as cur:
            cur.execute(
                """INSERT INTO plans
                   (plan_id, project_id, session_id, user_intent, dag,
                    approval_status, multimodal_refs)
                   VALUES (%s, %s, %s, %s, %s, 'approved', '[]')
                   ON CONFLICT (plan_id) DO UPDATE
                   SET dag = EXCLUDED.dag, approval_status = 'approved'
                """,
                (
                    plan["plan_id"],
                    plan["dag"][0]["project_id"],
                    TEST_SESSION,
                    plan["user_intent"],
                    json.dumps(plan["dag"]),
                ),
            )
        c.commit()
