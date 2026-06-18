import json
import os
import sqlite3
import time
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.db import queries

router = APIRouter(prefix="/api/sessions", tags=["sessions"])

AIONUI_DB = os.environ.get(
    "AIONUI_DB",
    "/home/aipc/.config/AionUi/aionui/aionui-backend.db",
)


def _resolve_team_info(
    conversation_id: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Resolve AionUi team info from conversation membership or workspace."""
    info = {"team_id": None, "workspace": None}
    try:
        conn = sqlite3.connect(f"file:{AIONUI_DB}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute("SELECT id, workspace, agents FROM teams ORDER BY updated_at DESC").fetchall()
            for row in rows:
                agents_raw = row["agents"]
                agents: list[dict[str, Any]] = []
                if isinstance(agents_raw, str):
                    try:
                        parsed = json.loads(agents_raw)
                        if isinstance(parsed, list):
                            agents = parsed
                    except json.JSONDecodeError:
                        agents = []
                if conversation_id and any(a.get("conversation_id") == conversation_id for a in agents):
                    return {"team_id": row["id"], "workspace": row["workspace"]}
                if session_id and row["workspace"] and session_id in row["workspace"]:
                    info = {"team_id": row["id"], "workspace": row["workspace"]}
        finally:
            conn.close()
    except (sqlite3.OperationalError, FileNotFoundError):
        pass
    return info


def _latest_signal_map() -> dict[str, dict[str, Any]]:
    with queries.conn() as c, c.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT ON (session_id)
                   session_id, ts, token_rate,
                   EXTRACT(EPOCH FROM last_activity) AS last_activity,
                   terminal, quota_suspected, pid_alive, fs_changed
              FROM session_signals
             ORDER BY session_id, ts DESC
            """
        )
        rows = [dict(row) for row in cur.fetchall()]
        return {str(row.get("session_id", "")): row for row in rows if row.get("session_id")}


def _node_id(node: dict[str, Any]) -> str:
    return str(node.get("id") or node.get("node_id") or "")


def _task_maps() -> tuple[dict[tuple[str, str], dict[str, Any]], dict[tuple[str, str], dict[str, Any]]]:
    with queries.conn() as c, c.cursor() as cur:
        cur.execute(
            """
            SELECT task_id, project_id, session_id, status, completion_signal,
                   plan_id, node_id, node_commit_tag, created_at, updated_at
              FROM tasks
             ORDER BY created_at ASC
            """
        )
        rows = [dict(row) for row in cur.fetchall()]
        by_session_node: dict[tuple[str, str], dict[str, Any]] = {}
        by_plan_node: dict[tuple[str, str], dict[str, Any]] = {}
        for row in rows:
            node_id = str(row.get("node_id", ""))
            if not node_id:
                continue
            session_id = str(row.get("session_id", ""))
            plan_id = str(row.get("plan_id", ""))
            if session_id:
                by_session_node[(session_id, node_id)] = row
            if plan_id:
                by_plan_node[(plan_id, node_id)] = row
        return by_session_node, by_plan_node


def _session_map(project_id: str | None = None) -> dict[str, dict[str, Any]]:
    with queries.conn() as c, c.cursor() as cur:
        if project_id:
            cur.execute("SELECT * FROM sessions WHERE project_id = %s ORDER BY created_at DESC", (project_id,))
        else:
            cur.execute("SELECT * FROM sessions ORDER BY created_at DESC")
        rows = [dict(row) for row in cur.fetchall()]
        return {str(row.get("session_id", "")): row for row in rows if row.get("session_id")}


def _plan_rows(project_id: str | None = None) -> list[dict[str, Any]]:
    with queries.conn() as c, c.cursor() as cur:
        if project_id:
            cur.execute(
                "SELECT plan_id, project_id, user_intent, goal, success, dag, created_at FROM plans WHERE project_id = %s ORDER BY created_at DESC",
                (project_id,),
            )
        else:
            cur.execute(
                "SELECT plan_id, project_id, user_intent, goal, success, dag, created_at FROM plans ORDER BY created_at DESC"
            )
        return [dict(row) for row in cur.fetchall()]


def _row_verdict(
    node_id: str,
    completed: set[str],
    deps: list[str],
    task: dict[str, Any] | None,
    active_node_id: str | None,
    sig: dict[str, Any] | None,
) -> str:
    if node_id in completed:
        return "done"
    if any(dep not in completed for dep in deps):
        return "to do"
    if active_node_id != node_id:
        return "to do"
    if sig:
        if sig.get("terminal"):
            return "done"
        # pid_alive is deliberately NOT used here — the ingest pipeline (which
        # owns most signal rows) has no knowledge of PIDs and defaults to
        # pid_alive=true after the Oct 2025 fix.  Only the watcher's own signal
        # rows carry authoritative pid_alive values, but the UI reads the latest
        # row regardless of origin.  The watcher enforces crash detection
        # server-side via verdict.py instead, and updates task.status accordingly.
        if sig.get("quota_suspected") and (sig.get("token_rate") or 0) == 0 and not sig.get("fs_changed"):
            return "quota"
        last_activity = sig.get("last_activity")
        if last_activity and (time.time() - float(last_activity)) > 120 and (sig.get("token_rate") or 0) == 0 and not sig.get("fs_changed"):
            return "stalled"
        return "running"
    if task and task.get("status") in {"open", "running", "active"}:
        return "running"
    return "to do"


def _node_title(node: dict[str, Any]) -> str:
    task = (node.get("task") or node.get("title") or node.get("description") or "").strip()
    if not task:
        return node.get("id", "—")
    return task[:80]


class SessionCreate(BaseModel):
    project_id: str
    session_id: str
    user_intent: Optional[str] = None
    base_branch: str = "main"


@router.get("")
async def list_sessions(project_id: Optional[str] = None):
    sessions = _session_map(project_id)
    tasks_by_session_node, tasks_by_plan_node = _task_maps()
    signals = _latest_signal_map()
    plans = _plan_rows(project_id)
    rows: list[dict[str, Any]] = []
    represented_session_ids: set[str] = set()

    for plan in plans:
        dag = plan.get("dag") or []
        if isinstance(dag, str):
            try:
                dag = json.loads(dag)
            except json.JSONDecodeError:
                dag = []
        if not isinstance(dag, list):
            dag = []

        completed = {
            _node_id(node)
            for node in dag
            if tasks_by_plan_node.get((plan["plan_id"], _node_id(node)), {}).get("status") == "done"
        }

        active_node_id: str | None = None
        for node in dag:
            nid = _node_id(node)
            if nid in completed:
                continue
            deps = node.get("depends_on", []) or []
            if all(dep in completed for dep in deps):
                task = tasks_by_plan_node.get((plan["plan_id"], nid))
                if task and task.get("status") != "done":
                    active_node_id = nid
                    break

        for idx, node in enumerate(dag, 1):
            nid = _node_id(node) or f"node-{idx}"
            deps = node.get("depends_on", []) or []
            task = tasks_by_plan_node.get((plan["plan_id"], nid))
            runtime_session_id = str(task.get("session_id")) if task and task.get("session_id") else plan["plan_id"]
            runtime_session = sessions.get(runtime_session_id, {})
            sig = signals.get(runtime_session_id)
            conversation_id = None
            if task:
                with queries.conn() as c, c.cursor() as cur:
                    cur.execute(
                        "SELECT aionui_conversation_id FROM aionui_links WHERE task_id = %s LIMIT 1",
                        (str(task["task_id"]),),
                    )
                    fetched = cur.fetchone()
                    row = dict(fetched) if fetched else None
                    conversation_id = row.get("aionui_conversation_id") if row else None
            lookup_session_id = runtime_session_id if (task and active_node_id == nid) else None
            team_info = _resolve_team_info(conversation_id=conversation_id, session_id=lookup_session_id)
            worktree_path = runtime_session.get("worktree_path") or team_info.get("workspace")
            worktree_label = os.path.basename(str(worktree_path)) if worktree_path else None
            verdict = _row_verdict(nid, completed, deps, task, active_node_id, sig)
            last_activity_s = None
            if sig and sig.get("last_activity"):
                last_activity_s = max(0.0, time.time() - float(sig["last_activity"]))
            if runtime_session_id:
                represented_session_ids.add(runtime_session_id)

            rows.append({
                "row_id": f"{plan['plan_id']}:{nid}",
                "session_id": runtime_session_id,
                "project_id": plan["project_id"],
                "user_intent": plan.get("user_intent"),
                "status": task.get("status") if task else "pending",
                "base_branch": runtime_session.get("base_branch") or "main",
                "created_at": task.get("created_at") if task else plan.get("created_at"),
                "aionui_team_id": team_info.get("team_id"),
                "watcher_verdict": verdict,
                "last_activity_s": last_activity_s,
                "token_rate": sig.get("token_rate") if sig and active_node_id == nid else None,
                "plan_title": plan["plan_id"],
                "active_node_id": nid,
                "active_node_title": _node_title(node),
                "node_commit_tag": task.get("node_commit_tag") if task else None,
                "score": None,
                "worktree_path": worktree_path,
                "worktree_label": worktree_label,
            })

    # Fallback: include non-plan standalone sessions only if they carry useful runtime data.
    planned_session_ids = {p["plan_id"] for p in plans}
    for sid, session in sessions.items():
        if sid in represented_session_ids:
            continue
        if sid in planned_session_ids:
            continue
        if not session.get("worktree_path"):
            continue
        rows.append({
            "row_id": sid,
            **session,
            "aionui_team_id": None,
            "watcher_verdict": session.get("status") or "unknown",
            "last_activity_s": None,
            "token_rate": None,
            "plan_title": None,
            "active_node_id": None,
            "active_node_title": None,
            "node_commit_tag": None,
            "score": None,
            "worktree_label": os.path.basename(str(session.get("worktree_path"))),
        })

    return rows


@router.post("")
async def create_session(req: SessionCreate):
    with queries.conn() as c, c.cursor() as cur:
        try:
            cur.execute(
                "INSERT INTO sessions (session_id, project_id, user_intent, base_branch) VALUES (%s,%s,%s,%s) RETURNING *",
                (req.session_id, req.project_id, req.user_intent, req.base_branch),
            )
            row = cur.fetchone()
            c.commit()
            return row
        except Exception as e:
            c.rollback()
            raise HTTPException(status_code=400, detail=str(e))
