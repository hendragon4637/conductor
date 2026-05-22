from typing import Optional
from uuid import UUID
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from backend.services import trigger_service


router = APIRouter(prefix="/api/triggers", tags=["triggers"])


class TriggerCreate(BaseModel):
    name: str
    project_id: str
    session_id: str
    agent_config_id: str
    trigger_type: str
    intent_template: str
    cron_expression: Optional[str] = None
    description: Optional[str] = None
    input_spec_override: Optional[dict] = None


@router.get("")
async def list_triggers(active_only: bool = True):
    return trigger_service.list_triggers(active_only=active_only)


@router.post("")
async def create_trigger(req: TriggerCreate):
    try:
        return trigger_service.create_trigger(**req.model_dump())
    except (ValueError, AssertionError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{trigger_id}")
async def get_trigger(trigger_id: UUID):
    t = trigger_service.get_trigger(trigger_id)
    if not t:
        raise HTTPException(status_code=404)
    return t


@router.delete("/{trigger_id}")
async def delete_trigger(trigger_id: UUID):
    ok = trigger_service.deactivate(trigger_id)
    if not ok:
        raise HTTPException(status_code=404)
    return {"trigger_id": str(trigger_id), "deactivated": True}


@router.post("/{trigger_id}/fire")
async def fire_now(trigger_id: UUID):
    return trigger_service.fire_trigger(trigger_id)
