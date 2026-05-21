from fastapi import APIRouter, HTTPException
from typing import Optional
from uuid import UUID
from backend.db import queries

router = APIRouter(prefix="/api/traces", tags=["traces"])


@router.get("")
async def list_traces(task_id: Optional[UUID] = None):
    with queries.conn() as c, c.cursor() as cur:
        if task_id:
            cur.execute("SELECT * FROM v_trace_summary WHERE task_id = %s ORDER BY started_at",
                        (str(task_id),))
        else:
            cur.execute("SELECT * FROM v_trace_summary ORDER BY started_at DESC LIMIT 100")
        return cur.fetchall()


@router.get("/{trace_id}")
async def get_trace(trace_id: UUID):
    with queries.conn() as c, c.cursor() as cur:
        cur.execute("SELECT * FROM traces WHERE trace_id = %s", (str(trace_id),))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404)
        cur.execute(
            "SELECT * FROM observations WHERE trace_id = %s ORDER BY step_index, started_at",
            (str(trace_id),),
        )
        row["observations"] = cur.fetchall()
        cur.execute("SELECT * FROM hitl_events WHERE trace_id = %s ORDER BY asked_at",
                    (str(trace_id),))
        row["hitl_events"] = cur.fetchall()
        cur.execute("SELECT * FROM scores WHERE trace_id = %s", (str(trace_id),))
        row["scores"] = cur.fetchall()
        return row
