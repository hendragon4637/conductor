from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from backend.db import queries

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


class SessionCreate(BaseModel):
    project_id: str
    session_id: str
    user_intent: Optional[str] = None
    base_branch: str = "main"


@router.get("")
async def list_sessions(project_id: Optional[str] = None):
    with queries.conn() as c, c.cursor() as cur:
        if project_id:
            cur.execute("SELECT * FROM sessions WHERE project_id = %s ORDER BY created_at DESC",
                        (project_id,))
        else:
            cur.execute("SELECT * FROM sessions ORDER BY created_at DESC")
        return cur.fetchall()


@router.post("")
async def create_session(req: SessionCreate):
    with queries.conn() as c, c.cursor() as cur:
        try:
            cur.execute(
                "INSERT INTO sessions (session_id, project_id, user_intent, base_branch) "
                "VALUES (%s,%s,%s,%s) RETURNING *",
                (req.session_id, req.project_id, req.user_intent, req.base_branch),
            )
            row = cur.fetchone()
            c.commit()
            return row
        except Exception as e:
            c.rollback()
            raise HTTPException(status_code=400, detail=str(e))
