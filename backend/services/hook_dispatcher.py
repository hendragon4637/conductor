from __future__ import annotations
import time
from typing import Any, Optional
from uuid import UUID

from backend.db import queries


VALID_EVENTS: set[str] = {
    "trace.pre_spawn", "trace.spawned", "trace.completed", "trace.failed",
    "trace.abandoned", "trace.labeled", "trace.scored",
}


def _match_filter(filter_obj: dict, trace_row: dict) -> bool:
    if not filter_obj:
        return True
    for k, expected in filter_obj.items():
        actual = trace_row.get(k)
        if isinstance(expected, list):
            if actual not in expected:
                return False
        elif actual != expected:
            return False
    return True


def dispatch(event: str, trace_id: UUID, trace_row: Optional[dict] = None) -> list[dict]:
    if event not in VALID_EVENTS:
        return [{"error": f"unknown event: {event}"}]

    if trace_row is None:
        with queries.conn() as c, c.cursor() as cur:
            cur.execute("SELECT * FROM traces WHERE trace_id = %s", (str(trace_id),))
            trace_row = cur.fetchone()
            if not trace_row:
                return [{"error": "trace not found", "trace_id": str(trace_id)}]

    with queries.conn() as c, c.cursor() as cur:
        cur.execute(
            """
            SELECT * FROM hooks
             WHERE event = %s AND active
             ORDER BY priority ASC, created_at ASC
            """,
            (event,),
        )
        hooks = cur.fetchall()

    summaries: list[dict] = []
    for h in hooks:
        filter_obj = h.get("filter") or {}
        if not _match_filter(filter_obj, trace_row):
            _record_invocation(h["hook_id"], trace_id, event, "skipped", "filter no-match")
            continue

        started = time.perf_counter()
        result_text = f"[STUB] would invoke action={h['action']} for hook={h['name']}"
        dur_ms = int((time.perf_counter() - started) * 1000)

        _record_invocation(h["hook_id"], trace_id, event, "logged", result_text, dur_ms)
        _bump_fire_count(h["hook_id"])

        summaries.append({
            "hook_id": h["hook_id"],
            "name": h["name"],
            "action": h["action"],
            "status": "logged",
            "summary": result_text,
        })

    return summaries


def _record_invocation(
    hook_id: str,
    trace_id: UUID,
    event: str,
    status: str,
    summary: str,
    duration_ms: Optional[int] = None,
) -> None:
    with queries.conn() as c, c.cursor() as cur:
        cur.execute(
            """
            INSERT INTO hook_invocations (hook_id, trace_id, event, status, result_summary, duration_ms)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (str(hook_id), str(trace_id), event, status, summary, duration_ms),
        )
        c.commit()


def _bump_fire_count(hook_id: str) -> None:
    with queries.conn() as c, c.cursor() as cur:
        cur.execute(
            """
            UPDATE hooks SET fire_count = fire_count + 1, last_fired_at = now()
             WHERE hook_id = %s
            """,
            (str(hook_id),),
        )
        c.commit()


def _execute_internal(action: str, payload: dict, trace_row: dict) -> dict:
    raise NotImplementedError("internal action execution is week 4+")


def _execute_webhook(url: str, payload: dict, trace_row: dict) -> dict:
    raise NotImplementedError("webhook action execution is week 5+")
