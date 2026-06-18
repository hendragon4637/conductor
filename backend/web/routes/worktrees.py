"""Worktree routes — list, create, remove git worktrees."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/worktrees", tags=["worktrees"])

_WORKSPACE_ROOT = Path(os.environ.get("WORKSPACE_ROOT", "/opt/aipc/conductor/workspace"))
_REPO_ROOT = Path(os.environ.get("REPO_ROOT", "/opt/aipc/conductor"))


class WorktreeCreate(BaseModel):
    branch: str
    project_id: Optional[str] = None


@router.get("")
async def list_worktrees():
    try:
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            capture_output=True, text=True, timeout=10,
            cwd=str(_REPO_ROOT),
        )
        worktrees: list[dict] = []
        current: dict = {}
        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                if current:
                    worktrees.append(current)
                    current = {}
                continue
            if line.startswith("worktree "):
                current["path"] = line[9:]
            elif line.startswith("HEAD "):
                current["head"] = line[5:]
            elif line.startswith("branch "):
                current["branch"] = line[7:]
            elif line.startswith("bare"):
                current["bare"] = True
        if current:
            worktrees.append(current)
        return {"worktrees": worktrees, "total": len(worktrees)}
    except Exception as exc:
        return {"worktrees": [], "total": 0, "error": str(exc)}


@router.post("")
async def create_worktree(req: WorktreeCreate):
    import uuid
    wt_name = req.project_id or f"wt-{uuid.uuid4().hex[:8]}"
    wt_path = _WORKSPACE_ROOT / wt_name
    try:
        subprocess.run(
            ["git", "worktree", "add", str(wt_path), req.branch],
            capture_output=True, text=True, timeout=30,
            cwd=str(_REPO_ROOT),
            check=True,
        )
        return {"path": str(wt_path), "branch": req.branch, "project_id": wt_name}
    except subprocess.CalledProcessError as e:
        raise HTTPException(400, detail=f"git worktree add failed: {e.stderr}")
    except Exception as e:
        raise HTTPException(500, detail=str(e))


@router.delete("/{path:path}")
async def remove_worktree(path: str):
    full = _WORKSPACE_ROOT / path
    try:
        subprocess.run(
            ["git", "worktree", "remove", str(full)],
            capture_output=True, text=True, timeout=30,
            cwd=str(_REPO_ROOT),
            check=True,
        )
        return {"removed": str(full)}
    except subprocess.CalledProcessError as e:
        raise HTTPException(400, detail=f"git worktree remove failed: {e.stderr}")
    except Exception as e:
        raise HTTPException(500, detail=str(e))
