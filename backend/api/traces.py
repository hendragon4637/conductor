from fastapi import APIRouter, HTTPException
from typing import Optional
from uuid import UUID
from pydantic import BaseModel
from backend.db import queries

router = APIRouter(prefix="/api/traces", tags=["traces"])


class PtyExitRequest(BaseModel):
    exit_code: int


@router.get("")
async def list_traces(task_id: Optional[UUID] = None):
    with queries.conn() as c, c.cursor() as cur:
        if task_id:
            cur.execute(
                "SELECT t.trace_id, t.task_id, t.agent_config_id, t.role, t.status, "
                "t.manual_label, t.failure_mode, t.cli_session_id, "
                "t.total_tokens, t.total_cost_usd, t.total_hitl, t.total_observations, "
                "t.started_at, t.ended_at, t.metadata, t.langfuse_trace_id, "
                "tk.session_id, tk.project_id, tk.user_intent, "
                "EXTRACT(EPOCH FROM (COALESCE(t.ended_at, now()) - t.started_at)) AS duration_s "
                "FROM legacy_traces t "
                "JOIN tasks tk ON tk.task_id = t.task_id "
                "WHERE t.task_id = %s ORDER BY t.started_at",
                (str(task_id),),
            )
        else:
            cur.execute(
                "SELECT t.trace_id, t.task_id, t.agent_config_id, t.role, t.status, "
                "t.manual_label, t.failure_mode, t.cli_session_id, "
                "t.total_tokens, t.total_cost_usd, t.total_hitl, t.total_observations, "
                "t.started_at, t.ended_at, t.metadata, t.langfuse_trace_id, "
                "tk.session_id, tk.project_id, tk.user_intent, "
                "EXTRACT(EPOCH FROM (COALESCE(t.ended_at, now()) - t.started_at)) AS duration_s "
                "FROM legacy_traces t "
                "JOIN tasks tk ON tk.task_id = t.task_id "
                "ORDER BY t.started_at DESC LIMIT 100"
            )
        return cur.fetchall()


@router.get("/{trace_id}")
async def get_trace(trace_id: UUID):
    with queries.conn() as c, c.cursor() as cur:
        cur.execute("SELECT * FROM legacy_traces WHERE trace_id = %s", (str(trace_id),))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404)
        cur.execute(
            "SELECT * FROM legacy_observations WHERE trace_id = %s ORDER BY step_index, started_at",
            (str(trace_id),),
        )
        row["observations"] = cur.fetchall()
        cur.execute("SELECT * FROM hitl_events WHERE trace_id = %s ORDER BY asked_at",
                    (str(trace_id),))
        row["hitl_events"] = cur.fetchall()
        cur.execute("SELECT * FROM legacy_scores WHERE trace_id = %s", (str(trace_id),))
        row["scores"] = cur.fetchall()
        return row


@router.post("/{trace_id}/pty-exit")
async def pty_exit(trace_id: UUID, body: PtyExitRequest):
    """Stores exit_code in traces.metadata JSONB. Does NOT change trace status
    — the trace lifecycle is managed by receipt/watchdog, not by PTY close.
    """
    with queries.conn() as c, c.cursor() as cur:
        cur.execute(
            "UPDATE legacy_traces SET metadata = COALESCE(metadata, '{}'::jsonb) || %s::jsonb"
            " WHERE trace_id = %s"
            " RETURNING trace_id",
            ('{"pty_exit_code": ' + str(body.exit_code) + "}", str(trace_id)),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="trace not found")
        c.commit()
    return {"ok": True, "exit_code": body.exit_code}


@router.post("/{trace_id}/resume-session")
async def resume_session(trace_id: UUID):
    with queries.conn() as c, c.cursor() as cur:
        cur.execute("""
            SELECT t.trace_id, t.task_id, t.agent_config_id, t.cli_session_id,
                   t.metadata, p.repo_path
            FROM legacy_traces t
            JOIN tasks tk ON tk.task_id = t.task_id
            JOIN projects p ON p.project_id = tk.project_id
            WHERE t.trace_id = %s
        """, (str(trace_id),))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="trace not found")

        cli_session_id = row["cli_session_id"]
        if not cli_session_id:
            raise HTTPException(status_code=400, detail="trace has no cli_session_id to resume")

        repo_path = row["repo_path"]

        pty_spec = {
            "command": "opencode",
            "args": ["-s", cli_session_id],
            "cwd": repo_path,
            "env": {
                "AIPC_TRACE_ID": str(trace_id),
                "AIPC_TASK_ID": str(row["task_id"]),
                "AIPC_AGENT_CONFIG_ID": row["agent_config_id"] or "",
                "AIPC_CLI_SESSION_ID": cli_session_id,
                "TERM": "xterm-256color",
            },
            "title": f"resume:{str(trace_id)[:8]}",
        }
        return {"pty_spec": pty_spec}
