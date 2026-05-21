"""Lightweight DB helpers used by graph nodes and API."""
from __future__ import annotations
import os
import hashlib
from pathlib import Path
from typing import Optional, Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row


DB_URL = os.environ["DATABASE_URL"]


def conn():
    return psycopg.connect(DB_URL, row_factory=dict_row)


def get_agent_config(agent_config_id: str) -> Optional[dict]:
    with conn() as c, c.cursor() as cur:
        cur.execute(
            "SELECT * FROM agent_configs WHERE agent_config_id = %s AND active",
            (agent_config_id,),
        )
        return cur.fetchone()


def get_task(task_id: UUID) -> Optional[dict]:
    with conn() as c, c.cursor() as cur:
        cur.execute("SELECT * FROM tasks WHERE task_id = %s", (str(task_id),))
        return cur.fetchone()


def hash_file(path: str) -> Optional[str]:
    """SHA256 hex digest of file content. Returns None if file missing."""
    p = Path(path)
    if not p.is_file():
        return None
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def insert_trace(
    *,
    task_id: UUID,
    agent_config_id: str,
    role: str,
    cli: str,
    input_spec: dict[str, Any],
    skill_path: Optional[str],
    skill_snapshot_hash: Optional[str],
    preceding_trace_id: Optional[UUID] = None,
) -> UUID:
    sql = """
    INSERT INTO traces (
      task_id, agent_config_id, role, cli, input_spec,
      skill_path, skill_snapshot_hash, preceding_trace_id, status
    ) VALUES (
      %s, %s, %s, %s, %s::jsonb,
      %s, %s, %s, 'pending'
    )
    RETURNING trace_id;
    """
    import json
    with conn() as c, c.cursor() as cur:
        cur.execute(
            sql,
            (
                str(task_id),
                agent_config_id,
                role,
                cli,
                json.dumps(input_spec),
                skill_path,
                skill_snapshot_hash,
                str(preceding_trace_id) if preceding_trace_id else None,
            ),
        )
        return cur.fetchone()["trace_id"]


def update_trace_status(
    trace_id: UUID,
    *,
    status: Optional[str] = None,
    output_spec: Optional[dict] = None,
    ended_reason: Optional[str] = None,
    cli_session_id: Optional[str] = None,
    cli_session_path: Optional[str] = None,
    total_tokens: Optional[int] = None,
    terminates_task: Optional[bool] = None,
) -> None:
    sets, params = [], []
    if status is not None:
        sets.append("status = %s"); params.append(status)
        if status in ("complete", "failed", "abandoned"):
            sets.append("ended_at = now()")
    if output_spec is not None:
        sets.append("output_spec = %s::jsonb")
        import json
        params.append(json.dumps(output_spec))
    if ended_reason is not None:
        sets.append("ended_reason = %s"); params.append(ended_reason)
    if cli_session_id is not None:
        sets.append("cli_session_id = %s"); params.append(cli_session_id)
    if cli_session_path is not None:
        sets.append("cli_session_path = %s"); params.append(cli_session_path)
    if total_tokens is not None:
        sets.append("total_tokens = %s"); params.append(total_tokens)
    if terminates_task is not None:
        sets.append("terminates_task = %s"); params.append(terminates_task)

    if not sets:
        return

    sql = f"UPDATE traces SET {', '.join(sets)} WHERE trace_id = %s"
    params.append(str(trace_id))
    with conn() as c, c.cursor() as cur:
        cur.execute(sql, params)


def mark_task_status(task_id: UUID, status: str, completion_signal: Optional[str] = None) -> None:
    with conn() as c, c.cursor() as cur:
        cur.execute(
            "UPDATE tasks SET status = %s, completion_signal = %s WHERE task_id = %s",
            (status, completion_signal, str(task_id)),
        )
