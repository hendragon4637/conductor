"""Smoke test: graph compiles, prepare_trace inserts row, completion updates row."""
import os
import uuid
import pytest
from dotenv import load_dotenv

load_dotenv()

from backend.db import queries
from backend.graph.state import ConductorState
from backend.graph.graph import prepare_graph, completion_graph


@pytest.fixture(scope="module")
def test_project_and_task():
    """Create a throwaway project + session + task for the test."""
    pid = "smoke-test"
    sid = "feat/smoke"

    with queries.conn() as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO projects (project_id, name, repo_path) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
            (pid, "Smoke Test", "/tmp/smoke-test"),
        )
        cur.execute(
            "INSERT INTO sessions (session_id, project_id, user_intent) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
            (sid, pid, "smoke test"),
        )
        cur.execute(
            "INSERT INTO tasks (project_id, session_id, user_intent) VALUES (%s,%s,%s) RETURNING task_id",
            (pid, sid, "smoke task"),
        )
        task_id = cur.fetchone()["task_id"]
        c.commit()

    yield pid, sid, task_id

    # Cleanup
    with queries.conn() as c, c.cursor() as cur:
        cur.execute("DELETE FROM tasks WHERE project_id = %s", (pid,))
        cur.execute("DELETE FROM sessions WHERE project_id = %s", (pid,))
        cur.execute("DELETE FROM projects WHERE project_id = %s", (pid,))
        c.commit()


def test_prepare_then_complete(test_project_and_task):
    pid, sid, task_id = test_project_and_task

    state = ConductorState(
        task_id=task_id,
        project_id=pid,
        session_id=sid,
        agent_config_id="opencode:backend-executor",
        input_spec={
            "spec_version": "1.0.0",
            "task_id": str(task_id),
            "user_intent": "smoke test",
            "domain": "backend",
            "intent_type": "test",
        },
    )

    # Run prepare flow
    result = prepare_graph.invoke(state)
    state = ConductorState(**result) if isinstance(result, dict) else result
    assert state.trace_id is not None
    assert state.status == "spawned"
    assert state.errors == []

    # Simulate CLI completing successfully with a valid output_spec
    state.status = "complete"
    state.output_spec = {
        "spec_version": "1.0.0",
        "task_id": str(task_id),
        "status": "completed",
        "summary": "smoke test pass",
    }

    result = completion_graph.invoke(state)
    state = ConductorState(**result) if isinstance(result, dict) else result

    # routing should terminate
    assert state.next_agent_config_id is None
    assert state.terminates_task is True

    # Trace should be marked complete
    with queries.conn() as c, c.cursor() as cur:
        cur.execute("SELECT status, output_spec FROM traces WHERE trace_id = %s", (str(state.trace_id),))
        row = cur.fetchone()
        assert row["status"] == "complete"
        assert row["output_spec"]["status"] == "completed"

    # Task should be done
    task = queries.get_task(task_id)
    assert task["status"] == "done"
    assert task["completion_signal"] == "manual_done"
