from fastapi import APIRouter, HTTPException
from pathlib import Path
from backend.services import skills_service


router = APIRouter(prefix="/api/skills", tags=["skills"])


@router.get("")
async def list_skills():
    return skills_service.discover_skills()


@router.get("/{skill_id}")
async def get_skill(skill_id: str):
    s = skills_service.find_by_skill_id(skill_id)
    if not s:
        raise HTTPException(status_code=404)
    return s


@router.post("/{skill_id}/rehash")
async def rehash(skill_id: str):
    s = skills_service.find_by_skill_id(skill_id)
    if not s:
        raise HTTPException(status_code=404)
    return skills_service.update_content_hash(Path(s["manifest_path"]))
