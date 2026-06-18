"""Ratchet routes — experiments, trigger run, pending approvals."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api/ratchet", tags=["ratchet"])


class RatchetRunRequest(BaseModel):
    threshold: float = 0.7
    min_runs: int = 1
    propose_only: bool = True
    max_tasks: int = 1


@router.get("/experiments")
async def list_experiments():
    try:
        import psycopg
        import os
        from dotenv import load_dotenv
        load_dotenv()
        db = os.environ["DATABASE_URL"]
        rows = []
        with psycopg.connect(db) as c:
            with c.cursor() as cur:
                cur.execute(
                    "SELECT experiment_id, agent_config_id, baseline_score, "
                    "candidate_score, (candidate_score - baseline_score) AS delta, "
                    "decision, created_at "
                    "FROM experiments ORDER BY created_at DESC LIMIT 50"
                )
                for row in cur.fetchall():
                    rows.append({
                        "experiment_id": row[0],
                        "agent_config": row[1],
                        "baseline_score": row[2],
                        "candidate_score": row[3],
                        "delta": row[4],
                        "decision": row[5],
                        "created_at": row[6].isoformat() if row[6] else None,
                    })
        return {"rows": rows, "total": len(rows)}
    except Exception as exc:
        return {"rows": [], "total": 0, "error": str(exc)}


@router.post("/run")
async def run_ratchet(req: RatchetRunRequest):
    from backend.triggers.jobs import ratchet_sweep
    result = ratchet_sweep({
        "threshold": req.threshold,
        "min_runs": req.min_runs,
        "propose_only": req.propose_only,
        "max_tasks": req.max_tasks,
    })
    return result


@router.get("/approvals")
async def pending_approvals():
    try:
        import psycopg
        import os
        from dotenv import load_dotenv
        load_dotenv()
        db = os.environ["DATABASE_URL"]
        rows = []
        with psycopg.connect(db) as c:
            with c.cursor() as cur:
                cur.execute(
                    "SELECT mutation_id, agent_config_id, skill_path, "
                    "kept, rationale, experiment_id, created_at "
                    "FROM skill_mutations WHERE kept IS NULL "
                    "ORDER BY created_at DESC LIMIT 20"
                )
                for row in cur.fetchall():
                    rows.append({
                        "mutation_id": row[0],
                        "agent_config": row[1],
                        "skill_path": row[2],
                        "kept": row[3],
                        "rationale": row[4],
                        "experiment_id": row[5],
                        "created_at": row[6].isoformat() if row[6] else None,
                    })
        return {"rows": rows, "total": len(rows)}
    except Exception as exc:
        return {"rows": [], "total": 0, "error": str(exc)}
