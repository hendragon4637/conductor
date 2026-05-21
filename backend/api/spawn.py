"""POST /api/spawn — spawn a new trace for a task."""
from __future__ import annotations
from uuid import UUID
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.services.spawn_service import spawn_for_task

router = APIRouter(prefix="/api/spawn", tags=["spawn"])


class SpawnRequest(BaseModel):
    task_id: UUID
    agent_config_id: str
    input_spec: Optional[dict] = None
    preceding_trace_id: Optional[UUID] = None


class SpawnResponse(BaseModel):
    trace_id: str
    cli_session_id: str
    repo_path: str
    branch: str


@router.post("", response_model=SpawnResponse)
async def spawn(req: SpawnRequest):
    try:
        result = spawn_for_task(
            task_id=req.task_id,
            agent_config_id=req.agent_config_id,
            input_spec=req.input_spec,
            preceding_trace_id=req.preceding_trace_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"spawn failed: {e}")
    return SpawnResponse(**result)
