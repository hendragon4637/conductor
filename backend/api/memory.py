from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from backend.services import memory_service


router = APIRouter(prefix="/api/memory", tags=["memory"])


class MemoryCreate(BaseModel):
    title: str
    body: str
    scope: str   # global | project | agent_config | session
    project_id: Optional[str] = None
    agent_config_id: Optional[str] = None
    session_id: Optional[str] = None
    tags: Optional[list[str]] = None
    source: str = "manual"


@router.get("")
async def list_memory(
    scope: Optional[str] = None,
    project_id: Optional[str] = None,
    agent_config_id: Optional[str] = None,
    session_id: Optional[str] = None,
):
    return memory_service.list_memory(
        scope=scope,
        project_id=project_id,
        agent_config_id=agent_config_id,
        session_id=session_id,
    )


@router.post("")
async def create_memory(req: MemoryCreate):
    try:
        return memory_service.create_memory(
            title=req.title,
            body=req.body,
            scope=req.scope,
            project_id=req.project_id,
            agent_config_id=req.agent_config_id,
            session_id=req.session_id,
            tags=req.tags,
            source=req.source,
        )
    except (ValueError, AssertionError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{memory_id}")
async def get_memory_body(memory_id: str):
    body = memory_service.read_memory_body(memory_id)
    if body is None:
        raise HTTPException(status_code=404)
    return {"memory_id": memory_id, "body": body}


@router.delete("/{memory_id}")
async def delete_memory(memory_id: str):
    ok = memory_service.deactivate_memory(memory_id)
    if not ok:
        raise HTTPException(status_code=404)
    return {"memory_id": memory_id, "deactivated": True}
