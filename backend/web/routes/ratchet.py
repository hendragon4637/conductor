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


@router.post("/approvals/{mutation_id}/approve")
async def approve_mutation(mutation_id: str):
    """Approve a pending mutation: set kept=TRUE and apply the mutation.

    For mutations produced by the evaluator-svc path (which stores the
    candidate prompt in ``diff``), we write the new prompt into the
    ``agent_configs`` table and bump the version.

    For mutations produced by the monolith path (filesystem-based), the
    candidate file has already been written during the experiment; we
    just update the flag.
    """
    try:
        import psycopg
        from psycopg.rows import dict_row
        import os
        from datetime import datetime, timezone
        from dotenv import load_dotenv
        load_dotenv()
        db = os.environ["DATABASE_URL"]

        with psycopg.connect(db, row_factory=dict_row) as c:
            with c.cursor() as cur:
                cur.execute(
                    "SELECT * FROM skill_mutations WHERE mutation_id = %s",
                    (mutation_id,),
                )
                mut = cur.fetchone()
                if mut is None:
                    return {"status": "error", "message": "mutation not found"}

                if mut["kept"] is not None:
                    return {
                        "status": "error",
                        "message": f"mutation already decided: kept={mut['kept']}",
                    }

                now = datetime.now(timezone.utc)
                cur.execute(
                    "UPDATE skill_mutations SET kept = TRUE, decision_at = %s "
                    "WHERE mutation_id = %s",
                    (now, mutation_id),
                )

                agent_config_id = mut["agent_config_id"]
                diff_content = mut.get("diff")
                if diff_content:
                    cur.execute(
                        "UPDATE agent_configs SET system_prompt = %s, "
                        "version = version + 1 WHERE agent_config_id = %s",
                        (diff_content, agent_config_id),
                    )

                c.commit()

                cur.execute(
                    "SELECT mutation_id, agent_config_id, skill_path, "
                    "kept, rationale, experiment_id, created_at, decision_at "
                    "FROM skill_mutations WHERE mutation_id = %s",
                    (mutation_id,),
                )
                row = cur.fetchone()
                return {
                    "mutation": {
                        "mutation_id": row["mutation_id"],
                        "agent_config": row["agent_config_id"],
                        "skill_path": row["skill_path"],
                        "kept": row["kept"],
                        "rationale": row["rationale"],
                        "experiment_id": row["experiment_id"],
                        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
                        "decision_at": row["decision_at"].isoformat() if row.get("decision_at") else None,
                    },
                    "status": "approved",
                }
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@router.post("/approvals/{mutation_id}/reject")
async def reject_mutation(mutation_id: str):
    """Reject a pending mutation: set kept=FALSE without applying."""
    try:
        import psycopg
        from psycopg.rows import dict_row
        import os
        from datetime import datetime, timezone
        from dotenv import load_dotenv
        load_dotenv()
        db = os.environ["DATABASE_URL"]

        with psycopg.connect(db, row_factory=dict_row) as c:
            with c.cursor() as cur:
                cur.execute(
                    "SELECT kept FROM skill_mutations WHERE mutation_id = %s",
                    (mutation_id,),
                )
                mut = cur.fetchone()
                if mut is None:
                    return {"status": "error", "message": "mutation not found"}

                if mut["kept"] is not None:
                    return {
                        "status": "error",
                        "message": f"mutation already decided: kept={mut['kept']}",
                    }

                now = datetime.now(timezone.utc)
                cur.execute(
                    "UPDATE skill_mutations SET kept = FALSE, decision_at = %s "
                    "WHERE mutation_id = %s",
                    (now, mutation_id),
                )
                c.commit()

                cur.execute(
                    "SELECT mutation_id, agent_config_id, skill_path, "
                    "kept, rationale, experiment_id, created_at, decision_at "
                    "FROM skill_mutations WHERE mutation_id = %s",
                    (mutation_id,),
                )
                row = cur.fetchone()
                return {
                    "mutation": {
                        "mutation_id": row["mutation_id"],
                        "agent_config": row["agent_config_id"],
                        "skill_path": row["skill_path"],
                        "kept": row["kept"],
                        "rationale": row["rationale"],
                        "experiment_id": row["experiment_id"],
                        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
                        "decision_at": row["decision_at"].isoformat() if row.get("decision_at") else None,
                    },
                    "status": "rejected",
                }
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
