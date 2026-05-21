from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from uuid import UUID
from backend.db import queries

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


class TaskCreate(BaseModel):
    project_id: str
    session_id: str
    user_intent: str


@router.get("")
async def list_tasks(project_id: Optional[str] = None, session_id: Optional[str] = None):
    with queries.conn() as c, c.cursor() as cur:
        if project_id and session_id:
            cur.execute(
                "SELECT * FROM tasks WHERE project_id = %s AND session_id = %s ORDER BY created_at DESC",
                (project_id, session_id),
            )
        elif project_id:
            cur.execute("SELECT * FROM tasks WHERE project_id = %s ORDER BY created_at DESC",
                        (project_id,))
        else:
            cur.execute("SELECT * FROM tasks ORDER BY created_at DESC LIMIT 100")
        return cur.fetchall()


@router.post("")
async def create_task(req: TaskCreate):
    with queries.conn() as c, c.cursor() as cur:
        try:
            cur.execute(
                "INSERT INTO tasks (project_id, session_id, user_intent) VALUES (%s,%s,%s) RETURNING *",
                (req.project_id, req.session_id, req.user_intent),
            )
            row = cur.fetchone()
            c.commit()
            return row
        except Exception as e:
            c.rollback()
            raise HTTPException(status_code=400, detail=str(e))


@router.get("/{task_id}")
async def get_task(task_id: UUID):
    task = queries.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404)
    return task
