"""intake_intents CRUD, dedupe, and correlation-by-plan_id.

All functions use raw psycopg (same pattern as planner's backend.db.queries).
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


def _db() -> str:
    return os.environ["DATABASE_URL"]


def insert_intent(intent: dict, status: str = "proposed",
                  last_error: str | None = None) -> dict:
    """Insert a new intake_intent row and return it (with generated id)."""
    import psycopg
    from psycopg.rows import dict_row

    intent_text = intent.get("intent_text", "")
    evidence = json.dumps(intent.get("evidence", []))
    now = datetime.now(timezone.utc)

    with psycopg.connect(_db(), row_factory=dict_row) as c:
        with c.cursor() as cur:
            cur.execute(
                """INSERT INTO intake_intents
                   (origin, source_ref, project_id, intent_text, evidence,
                    status, attempt, last_error, clarify_rounds, created_at, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   RETURNING id, origin, source_ref, project_id, intent_text,
                             evidence, status, attempt, plan_id, last_error,
                             clarify_rounds, created_at, updated_at""",
                (
                    intent.get("origin", ""),
                    intent.get("source_ref"),
                    intent.get("project_id", "default"),
                    intent_text,
                    evidence,
                    status,
                    intent.get("attempt", 1),
                    last_error,
                    intent.get("clarify_rounds", 0),
                    now, now,
                ),
            )
            row = cur.fetchone()
        c.commit()

    if row is None:
        raise RuntimeError("insert_intent returned no row")

    return _row_to_dict(row)


def update_intent(intent_id: int, **kwargs) -> None:
    """Update an intake_intent row. Kwargs map to column names."""
    import psycopg

    if not kwargs:
        return
    sets = ", ".join(f"{k} = %s" for k in kwargs)
    vals = list(kwargs.values())
    vals.append(intent_id)

    with psycopg.connect(_db()) as c:
        with c.cursor() as cur:
            cur.execute(
                f"UPDATE intake_intents SET {sets}, updated_at = now() WHERE id = %s",
                vals,
            )
        c.commit()


def load_intent_by_plan(plan_id: str) -> dict | None:
    """Load the most recent intent correlated to a plan_id."""
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(_db(), row_factory=dict_row) as c:
        with c.cursor() as cur:
            cur.execute(
                """SELECT * FROM intake_intents
                   WHERE plan_id = %s
                   ORDER BY updated_at DESC LIMIT 1""",
                (plan_id,),
            )
            return cur.fetchone()


def load_intent_by_source_ref(source_ref: str) -> dict | None:
    """Load the most recent intent matching a source_ref."""
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(_db(), row_factory=dict_row) as c:
        with c.cursor() as cur:
            cur.execute(
                """SELECT * FROM intake_intents
                   WHERE source_ref = %s
                   ORDER BY updated_at DESC LIMIT 1""",
                (source_ref,),
            )
            return cur.fetchone()


def oldest_proposed(project_id: str) -> dict | None:
    """Return the oldest 'proposed' intent for a project."""
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(_db(), row_factory=dict_row) as c:
        with c.cursor() as cur:
            cur.execute(
                """SELECT * FROM intake_intents
                   WHERE project_id = %s AND status = 'proposed'
                   ORDER BY created_at ASC LIMIT 1""",
                (project_id,),
            )
            return cur.fetchone()


def query_intents(project_id: str | None = None,
                  status: str | None = None) -> list[dict]:
    """List intake_intents with optional filters."""
    import psycopg
    from psycopg.rows import dict_row

    clauses = []
    params = []

    if project_id:
        clauses.append("project_id = %s")
        params.append(project_id)
    if status:
        clauses.append("status = %s")
        params.append(status)

    where = "WHERE " + " AND ".join(clauses) if clauses else ""

    with psycopg.connect(_db(), row_factory=dict_row) as c:
        with c.cursor() as cur:
            cur.execute(
                f"SELECT * FROM intake_intents {where} ORDER BY created_at DESC LIMIT 100",
                params,
            )
            return cur.fetchall()


def is_duplicate(intent: dict, window_days: int = 7) -> bool:
    """Check if intent duplicates a recent open intent.

    Reformulations (attempt > 1) are always allowed.
    """
    if intent.get("attempt", 1) > 1:
        return False

    import psycopg

    source_ref = intent.get("source_ref")
    if not source_ref:
        return False

    with psycopg.connect(_db()) as c:
        with c.cursor() as cur:
            cur.execute(
                """SELECT count(*) FROM intake_intents
                   WHERE project_id = %s
                     AND source_ref = %s
                     AND status IN ('proposed','submitted','clarifying','awaiting_ratify','running')
                     AND created_at > now() - interval '1 day' * %s
                     AND attempt = 1""",
                (intent.get("project_id", "default"), source_ref, window_days),
            )
            row = cur.fetchone()
            return (row[0] or 0) > 0


def _row_to_dict(row: dict) -> dict[str, Any]:
    """Post-process a dict_row: deserialize JSONB and convert datetimes."""
    d = dict(row)
    for field in ("evidence",):
        if field in d and isinstance(d[field], str):
            d[field] = json.loads(d[field])
    for field in ("created_at", "updated_at"):
        if field in d and hasattr(d[field], "isoformat"):
            d[field] = d[field].isoformat()
    return d
