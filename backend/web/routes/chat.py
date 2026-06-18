"""Chat routes — threads, messages, promote-to-plan.

Persistence:
  - Threads and messages are stored in the DB (chat_threads / chat_messages).
  - Pre-approval plan state lives in ``backend.web.routes.plan._plans`` (in-memory
    editing buffer), saved to the DB ``plans`` table on approve.
"""
from __future__ import annotations

import json
import os
import urllib.request
import uuid
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.db import queries
from backend.planning.schema import Plan
from backend.planning.store import save_plan

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ThreadCreate(BaseModel):
    title: Optional[str] = "New Chat"
    project_id: Optional[str] = None
    model: Optional[str] = "deepseek-v4-flash"


class ThreadUpdate(BaseModel):
    model: Optional[str] = None
    project_ids: Optional[list[str]] = None


class MessageSend(BaseModel):
    content: str
    role: str = "user"
    project_ids: Optional[list[str]] = None


class PromoteRequest(BaseModel):
    thread_id: str
    message_ids: list[str]
    project_id: Optional[str] = None
    project_ids: Optional[list[str]] = None


def _jd(val: Any) -> str:
    return json.dumps(val)


def _load_thread(thread_id: str) -> dict[str, Any] | None:
    with queries.conn() as c, c.cursor() as cur:
        cur.execute(
            "SELECT thread_id, title, project_ids, project_ids->>0 AS project_id, model, created_at FROM chat_threads WHERE thread_id = %s",
            (thread_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        thread = dict(row)
        thread["project_ids"] = thread.get("project_ids") or []
        return thread


def _load_messages(thread_id: str) -> list[dict[str, Any]]:
    with queries.conn() as c, c.cursor() as cur:
        cur.execute(
            "SELECT message_id, thread_id, role, content, created_at FROM chat_messages WHERE thread_id = %s ORDER BY created_at ASC",
            (thread_id,),
        )
        result = []
        for row in cur.fetchall():
            msg = dict(row)
            raw = msg.pop("content", None)
            if isinstance(raw, str):
                msg["content"] = raw
            elif isinstance(raw, dict) and "text" in raw:
                msg["content"] = raw["text"]
            else:
                msg["content"] = str(raw) if raw is not None else ""
            result.append(msg)
        return result


@router.get("/threads")
async def list_threads():
    with queries.conn() as c, c.cursor() as cur:
        cur.execute(
            "SELECT thread_id, title, project_ids, project_ids->>0 AS project_id, model, created_at FROM chat_threads ORDER BY created_at DESC"
        )
        rows = []
        for row in cur.fetchall():
            item = dict(row)
            item["project_ids"] = item.get("project_ids") or []
            rows.append(item)
        return rows


@router.post("/threads")
async def create_thread(req: ThreadCreate):
    tid = str(uuid.uuid4())
    project_ids = _jd([req.project_id]) if req.project_id else _jd([])
    with queries.conn() as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO chat_threads (thread_id, title, project_ids, model) VALUES (%s, %s, %s::jsonb, %s)",
            (tid, req.title or "New Chat", project_ids, req.model),
        )
        c.commit()
    return {"thread_id": tid, "title": req.title or "New Chat",
            "project_id": req.project_id, "project_ids": [req.project_id] if req.project_id else [], "model": req.model,
            "created_at": __import__("datetime").datetime.now().isoformat()}


@router.put("/threads/{thread_id}")
async def update_thread(thread_id: str, req: ThreadUpdate):
    thread = _load_thread(thread_id)
    if not thread:
        raise HTTPException(404, "Thread not found")
    project_ids = req.project_ids if req.project_ids is not None else thread.get("project_ids") or []
    model = req.model if req.model is not None else thread.get("model")
    with queries.conn() as c, c.cursor() as cur:
        cur.execute(
            "UPDATE chat_threads SET model = %s, project_ids = %s::jsonb WHERE thread_id = %s",
            (model, _jd(project_ids), thread_id),
        )
        c.commit()
    updated = _load_thread(thread_id)
    if not updated:
        raise HTTPException(404, "Thread not found")
    updated["messages"] = _load_messages(thread_id)
    return updated


@router.get("/threads/{thread_id}")
async def get_thread(thread_id: str):
    thread = _load_thread(thread_id)
    if not thread:
        raise HTTPException(404, "Thread not found")
    thread["messages"] = _load_messages(thread_id)
    return thread


@router.post("/threads/{thread_id}/messages")
async def send_message(thread_id: str, req: MessageSend):
    thread = _load_thread(thread_id)
    if not thread:
        raise HTTPException(404, "Thread not found")

    msg_id = f"msg-{uuid.uuid4().hex[:12]}"
    now = __import__("datetime").datetime.now().isoformat()
    with queries.conn() as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO chat_messages (message_id, thread_id, role, content, created_at) VALUES (%s, %s, %s, %s::jsonb, NOW())",
            (msg_id, thread_id, req.role, _jd(req.content)),
        )
        c.commit()
    msg = {"message_id": msg_id, "thread_id": thread_id, "role": req.role,
           "content": req.content, "created_at": now}

    endpoint = os.environ.get("BRAIN_ENDPOINT", "http://127.0.0.1:8001/v3")
    model_name = thread.get("model") or os.environ.get("BRAIN_MODEL", "local-test")
    reply_content = ""
    try:
        body = json.dumps({
            "model": model_name,
            "messages": [{"role": "user", "content": req.content}],
            "max_tokens": 512,
            "temperature": 0.7,
        }).encode()
        llm_req = urllib.request.Request(
            f"{endpoint}/chat/completions",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(llm_req, timeout=120) as resp:
            result = json.loads(resp.read())
            reply_content = result["choices"][0]["message"]["content"]
    except Exception as exc:
        reply_content = f"[LLM call failed: {exc}]"

    reply_id = f"msg-{uuid.uuid4().hex[:12]}"
    with queries.conn() as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO chat_messages (message_id, thread_id, role, content, created_at) VALUES (%s, %s, %s, %s::jsonb, NOW())",
            (reply_id, thread_id, "assistant", _jd(reply_content)),
        )
        c.commit()
    reply = {"message_id": reply_id, "thread_id": thread_id, "role": "assistant",
             "content": reply_content, "created_at": __import__("datetime").datetime.now().isoformat()}
    return {"user_message": msg, "assistant_message": reply}


@router.post("/promote-to-plan")
async def promote_to_plan(req: PromoteRequest):
    """Promote selected messages from a chat thread into a pending plan."""
    thread = _load_thread(req.thread_id)
    if not thread:
        raise HTTPException(404, "Thread not found")
    msgs = _load_messages(req.thread_id)
    selected = [m for m in msgs if m["message_id"] in req.message_ids]
    if not selected:
        raise HTTPException(400, "No matching messages found")
    request_project_ids = req.project_ids or []
    thread_project_ids = thread.get("project_ids") or []
    selected_project_ids = request_project_ids or thread_project_ids
    persist_project_id = req.project_id or (selected_project_ids[0] if selected_project_ids else None) or thread.get("project_id")
    project_part = "-".join(selected_project_ids) if selected_project_ids else "no-project"
    plan_id = f"plan-from-{project_part}-{uuid.uuid4().hex[:8]}"
    user_intent = "\n".join(m.get("content", "") for m in selected if m.get("role") == "user")
    plan = {
        "plan_id": plan_id,
        "source_thread": req.thread_id,
        "messages": selected,
        "status": "pending",
        "created_at": __import__("datetime").datetime.now().isoformat(),
    }
    from backend.web.routes.plan import _plans as plan_store
    plan_store[plan_id] = {**plan, "nodes": [], "title": f"Plan from {req.thread_id}",
                           "description": user_intent or None,
                           "worktree_id": None, "project_id": persist_project_id}
    if persist_project_id:
        save_plan(Plan(
            plan_id=plan_id,
            project_id=persist_project_id,
            session_id=None,
            user_intent=user_intent or "",
            nodes=[],
        ))
    return plan
