from typing import Optional
from uuid import UUID
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from backend.db import queries


router = APIRouter(prefix="/api/hooks", tags=["hooks"])


class HookCreate(BaseModel):
    name: str
    event: str
    action: str
    description: Optional[str] = None
    filter: Optional[dict] = None
    action_payload: Optional[dict] = None
    priority: int = 100


@router.get("")
async def list_hooks(active_only: bool = True, event: Optional[str] = None):
    sql = "SELECT * FROM hooks WHERE TRUE"
    params: list = []
    if active_only:
        sql += " AND active"
    if event:
        sql += " AND event = %s"
        params.append(event)
    sql += " ORDER BY event, priority, name"
    with queries.conn() as c, c.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


@router.post("")
async def create_hook(req: HookCreate):
    import json
    with queries.conn() as c, c.cursor() as cur:
        try:
            cur.execute(
                """
                INSERT INTO hooks (name, description, event, filter, action,
                                   action_payload, priority, active)
                VALUES (%s, %s, %s, %s::jsonb, %s, %s::jsonb, %s, TRUE)
                RETURNING *
                """,
                (req.name, req.description, req.event,
                 json.dumps(req.filter or {}),
                 req.action,
                 json.dumps(req.action_payload) if req.action_payload else None,
                 req.priority),
            )
            row = cur.fetchone()
            c.commit()
            return row
        except Exception as e:
            c.rollback()
            raise HTTPException(status_code=400, detail=str(e))


@router.get("/{hook_id}")
async def get_hook(hook_id: UUID):
    with queries.conn() as c, c.cursor() as cur:
        cur.execute("SELECT * FROM hooks WHERE hook_id = %s", (str(hook_id),))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404)
        return row


@router.delete("/{hook_id}")
async def delete_hook(hook_id: UUID):
    with queries.conn() as c, c.cursor() as cur:
        cur.execute(
            "UPDATE hooks SET active = FALSE WHERE hook_id = %s RETURNING hook_id",
            (str(hook_id),),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404)
        c.commit()
        return {"hook_id": str(hook_id), "deactivated": True}


@router.get("/{hook_id}/invocations")
async def list_invocations(hook_id: UUID, limit: int = 50):
    with queries.conn() as c, c.cursor() as cur:
        cur.execute(
            """
            SELECT * FROM hook_invocations
             WHERE hook_id = %s
             ORDER BY dispatched_at DESC
             LIMIT %s
            """,
            (str(hook_id), limit),
        )
        return cur.fetchall()
