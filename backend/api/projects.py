from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional
import os
from pathlib import Path
from backend.db import queries

router = APIRouter(prefix="/api/projects", tags=["projects"])
WORKSPACE_ROOT = Path(os.environ["WORKSPACE_ROOT"])


class ProjectCreate(BaseModel):
    project_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*[a-z0-9]$")
    name: str
    description: Optional[str] = None
    system_prompt: Optional[str] = None


@router.get("")
async def list_projects():
    with queries.conn() as c, c.cursor() as cur:
        cur.execute("SELECT * FROM projects WHERE NOT archived ORDER BY created_at DESC")
        return cur.fetchall()


@router.post("")
async def create_project(req: ProjectCreate):
    repo_path = str(WORKSPACE_ROOT / req.project_id)
    with queries.conn() as c, c.cursor() as cur:
        try:
            cur.execute(
                "INSERT INTO projects (project_id, name, description, system_prompt, repo_path) "
                "VALUES (%s,%s,%s,%s,%s) RETURNING *",
                (req.project_id, req.name, req.description, req.system_prompt, repo_path),
            )
            row = cur.fetchone()
            c.commit()
            return row
        except Exception as e:
            c.rollback()
            raise HTTPException(status_code=400, detail=str(e))
