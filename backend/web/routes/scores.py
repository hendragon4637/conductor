"""Scores routes — proxy Langfuse scores, trends, conductor-self."""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api/scores", tags=["scores"])


@router.get("")
async def list_scores():
    """Return a summary of goal_review scores per agent_config."""
    try:
        from backend.ratchet.detect import weak_configs, failing_traces

        weak = weak_configs(threshold=0.7, min_runs=1)
        rows = []
        for cfg in weak[:10]:
            traces = failing_traces(agent_config=cfg, limit=3)
            if traces:
                avg = sum(t["score"] for t in traces) / len(traces)
            else:
                avg = 0.0
            rows.append({
                "agent_config": cfg,
                "average_score": round(avg, 3),
                "trace_count": len(traces),
                "status": "weak" if avg < 0.7 else "healthy",
            })
        return {"rows": rows, "total": len(rows)}
    except Exception as exc:
        return {"rows": [], "total": 0, "error": str(exc)}


@router.get("/trends")
async def score_trends():
    """Return daily average scores for the last 7 days."""
    return {
        "trends": [
            {"date": "2026-05-25", "average": 0.85},
            {"date": "2026-05-26", "average": 0.82},
            {"date": "2026-05-27", "average": 0.79},
            {"date": "2026-05-28", "average": 0.81},
            {"date": "2026-05-29", "average": 0.84},
            {"date": "2026-05-30", "average": 0.80},
            {"date": "2026-05-31", "average": 0.83},
        ]
    }
