from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from uuid import UUID
from backend.db import queries

router = APIRouter(prefix="/api/labels", tags=["labels"])


class LabelRequest(BaseModel):
    manual_label: str
    failure_mode: Optional[str] = None
    manual_notes: Optional[str] = None


@router.post("/{trace_id}")
async def label_trace(trace_id: UUID, req: LabelRequest):
    if req.manual_label not in ("pass", "fail", "partial"):
        raise HTTPException(status_code=400, detail="manual_label must be pass|fail|partial")
    with queries.conn() as c, c.cursor() as cur:
        cur.execute(
            """UPDATE traces SET manual_label = %s, failure_mode = %s,
                   manual_notes = %s, labeled_at = now() WHERE trace_id = %s
               RETURNING trace_id, manual_label, failure_mode""",
            (req.manual_label, req.failure_mode, req.manual_notes, str(trace_id)),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404)
        c.commit()

    # Lifecycle event: trace was labeled
    try:
        from backend.services.hook_dispatcher import dispatch
        dispatch("trace.labeled", trace_id)
    except Exception:
        pass

    return row
