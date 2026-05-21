"""Integration test: hand-create a fake OpenCode SQLite session and verify ingest."""
import json
import os
import sqlite3
import tempfile
import uuid
from pathlib import Path
import pytest
from dotenv import load_dotenv

# Load env before importing backend modules
load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

# Override DB path to a temp file
TMP_DB = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
TMP_DB.close()
os.environ["OPENCODE_DB_PATH"] = TMP_DB.name

from backend.db import queries
from backend.services.opencode_adapter import ingest_trace, extract_receipt


def _make_db():
    """Create a fake OpenCode SQLite DB matching the real schema subset."""
    conn = sqlite3.connect(TMP_DB.name)
    conn.execute("""
        CREATE TABLE session (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            slug TEXT NOT NULL,
            directory TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL DEFAULT '',
            version TEXT NOT NULL DEFAULT '',
            time_created INTEGER NOT NULL,
            time_updated INTEGER NOT NULL,
            agent TEXT,
            model TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE message (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            time_created INTEGER NOT NULL,
            time_updated INTEGER NOT NULL,
            data TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE part (
            id TEXT PRIMARY KEY,
            message_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            time_created INTEGER NOT NULL,
            time_updated INTEGER NOT NULL,
            data TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()


def test_extract_receipt_simple():
    text = 'Here is the result.\n__CONTRIBUTION_RECEIPT__:{"spec_version":"1.0.0","task_id":"0000","status":"completed","summary":"ok"}\nthanks'
    r = extract_receipt(text)
    assert r is not None
    assert r["status"] == "completed"


def test_extract_receipt_with_nested_braces():
    text = '__CONTRIBUTION_RECEIPT__:{"spec_version":"1.0.0","task_id":"0","status":"completed","summary":"x","files_changed":[{"path":"a.py","operation":"created"}]}'
    r = extract_receipt(text)
    assert r is not None
    assert len(r["files_changed"]) == 1


def test_extract_receipt_missing():
    assert extract_receipt("no receipt here") is None


def test_ingest_creates_observations():
    """End-to-end: write fake OpenCode SQLite, trace row, ingest, verify."""

    pid = f"adapter-test-{uuid.uuid4().hex[:6]}"
    sid_branch = "feat/test"
    cli_sid = f"sess_{uuid.uuid4().hex[:20]}"
    now_ms = 1716191234567

    # Build fake OpenCode DB
    _make_db()
    conn = sqlite3.connect(TMP_DB.name)

    conn.execute(
        "INSERT INTO session (id, project_id, slug, time_created, time_updated, agent, model) VALUES (?,?,?,?,?,?,?)",
        (cli_sid, "proj1", "test-session", now_ms, now_ms + 1000, "test-agent",
         json.dumps({"id": "minimax-m2.5-free", "providerID": "opencode"})),
    )

    msg_id = f"msg_{uuid.uuid4().hex[:20]}"
    msg_data = json.dumps({
        "role": "assistant",
        "agent": "test-agent",
        "model": {"providerID": "opencode", "modelID": "minimax-m2.5-free"},
    })
    conn.execute(
        "INSERT INTO message (id, session_id, time_created, time_updated, data) VALUES (?,?,?,?,?)",
        (msg_id, cli_sid, now_ms + 100, now_ms + 100, msg_data),
    )

    part_id = f"part_{uuid.uuid4().hex[:20]}"
    part_data = json.dumps({
        "type": "text",
        "text": 'done.\n__CONTRIBUTION_RECEIPT__:{"spec_version":"1.0.0","task_id":"00000000-0000-0000-0000-000000000001","status":"completed","summary":"ok"}',
    })
    conn.execute(
        "INSERT INTO part (id, message_id, session_id, time_created, time_updated, data) VALUES (?,?,?,?,?,?)",
        (part_id, msg_id, cli_sid, now_ms + 200, now_ms + 200, part_data),
    )
    conn.commit()
    conn.close()

    # Create Conductor-side project, session, task, trace
    with queries.conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO projects (project_id, name, repo_path) VALUES (%s,%s,%s)",
                    (pid, pid, "/tmp/" + pid))
        cur.execute("INSERT INTO sessions (session_id, project_id, user_intent) VALUES (%s,%s,%s)",
                    (sid_branch, pid, "test"))
        cur.execute(
            "INSERT INTO tasks (project_id, session_id, user_intent) VALUES (%s,%s,%s) RETURNING task_id",
            (pid, sid_branch, "test"),
        )
        task_id = cur.fetchone()["task_id"]
        cur.execute(
            """INSERT INTO traces (task_id, agent_config_id, role, cli, cli_session_id, status)
               VALUES (%s,'opencode:backend-executor','executor','opencode',%s,'running') RETURNING trace_id""",
            (task_id, cli_sid),
        )
        trace_id = cur.fetchone()["trace_id"]
        c.commit()

    # Build trace dict for the adapter
    trace = dict(
        trace_id=trace_id,
        task_id=task_id,
        agent_config_id="opencode:backend-executor",
        cli_session_id=cli_sid,
        input_spec={},
    )

    summary = ingest_trace(trace)
    assert summary["session_found"]
    assert summary["new_observations"] >= 1
    assert summary["receipt_found"]
    assert summary["completion_triggered"]

    # Verify trace marked complete
    with queries.conn() as c, c.cursor() as cur:
        cur.execute("SELECT status, output_spec FROM traces WHERE trace_id = %s", (str(trace_id),))
        row = cur.fetchone()
        assert row["status"] == "complete"
        assert row["output_spec"]["status"] == "completed"

    # Cleanup
    with queries.conn() as c, c.cursor() as cur:
        cur.execute("DELETE FROM observations WHERE trace_id = %s", (str(trace_id),))
        cur.execute("DELETE FROM traces WHERE trace_id = %s", (str(trace_id),))
        cur.execute("DELETE FROM tasks WHERE project_id = %s", (pid,))
        cur.execute("DELETE FROM sessions WHERE project_id = %s", (pid,))
        cur.execute("DELETE FROM projects WHERE project_id = %s", (pid,))
        c.commit()

    os.unlink(TMP_DB.name)
