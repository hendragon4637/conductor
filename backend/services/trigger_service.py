from __future__ import annotations
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from croniter import croniter

from backend.db import queries
from backend.services.spawn_service import spawn_for_task


# ──────────────────────── compute next fire time ────────────────────────

def compute_next_fire(cron_expr: str, base: Optional[datetime] = None) -> datetime:
    base = base or datetime.now(timezone.utc)
    itr = croniter(cron_expr, base)
    return itr.get_next(datetime)


# ──────────────────────── CRUD ────────────────────────

def create_trigger(
    *,
    name: str,
    project_id: str,
    session_id: str,
    agent_config_id: str,
    trigger_type: str,
    intent_template: str,
    cron_expression: Optional[str] = None,
    description: Optional[str] = None,
    input_spec_override: Optional[dict] = None,
) -> dict:
    if trigger_type == "cron" and not cron_expression:
        raise ValueError("cron trigger requires cron_expression")

    next_fire = (
        compute_next_fire(cron_expression).isoformat()
        if (trigger_type == "cron" and cron_expression)
        else None
    )

    import json
    with queries.conn() as c, c.cursor() as cur:
        cur.execute(
            """
            INSERT INTO triggers (
              name, description, trigger_type,
              project_id, session_id, agent_config_id,
              cron_expression, next_fire_at,
              intent_template, input_spec_override, active
            )
            VALUES (%s,%s,%s, %s,%s,%s, %s,%s, %s,%s::jsonb,TRUE)
            RETURNING *
            """,
            (
                name, description, trigger_type,
                project_id, session_id, agent_config_id,
                cron_expression, next_fire,
                intent_template,
                json.dumps(input_spec_override) if input_spec_override else None,
            ),
        )
        row = cur.fetchone()
        c.commit()
        return row


def list_triggers(active_only: bool = True) -> list[dict]:
    sql = "SELECT * FROM triggers"
    if active_only:
        sql += " WHERE active"
    sql += " ORDER BY next_fire_at NULLS LAST, name"
    with queries.conn() as c, c.cursor() as cur:
        cur.execute(sql)
        return cur.fetchall()


def get_trigger(trigger_id: UUID) -> Optional[dict]:
    with queries.conn() as c, c.cursor() as cur:
        cur.execute("SELECT * FROM triggers WHERE trigger_id = %s", (str(trigger_id),))
        return cur.fetchone()


def deactivate(trigger_id: UUID) -> bool:
    with queries.conn() as c, c.cursor() as cur:
        cur.execute(
            "UPDATE triggers SET active = FALSE WHERE trigger_id = %s RETURNING trigger_id",
            (str(trigger_id),),
        )
        return cur.fetchone() is not None


# ──────────────────────── firing ────────────────────────

def fire_trigger(trigger_id: UUID) -> dict:
    trig = get_trigger(trigger_id)
    if not trig:
        return {"error": "trigger not found"}
    if not trig["active"]:
        return {"error": "trigger inactive"}

    # 1) Create the task
    with queries.conn() as c, c.cursor() as cur:
        cur.execute(
            """
            INSERT INTO tasks (project_id, session_id, user_intent, triggered_by)
            VALUES (%s,%s,%s,%s)
            RETURNING task_id
            """,
            (trig["project_id"], trig["session_id"], trig["intent_template"], trig["trigger_id"]),
        )
        task_id = cur.fetchone()["task_id"]
        c.commit()

    # 2) Spawn
    result = spawn_for_task(
        task_id=task_id,
        agent_config_id=trig["agent_config_id"],
        input_spec=trig.get("input_spec_override"),
    )

    # 3) Update trigger fire_count + next_fire_at
    now_iso = datetime.now(timezone.utc).isoformat()
    next_fire = None
    if trig["trigger_type"] == "cron" and trig["cron_expression"]:
        next_fire = compute_next_fire(trig["cron_expression"]).isoformat()

    with queries.conn() as c, c.cursor() as cur:
        cur.execute(
            """
            UPDATE triggers
               SET last_fired_at = %s,
                   next_fire_at = %s,
                   fire_count = fire_count + 1
             WHERE trigger_id = %s
            """,
            (now_iso, next_fire, str(trigger_id)),
        )
        c.commit()

    return {"trigger_id": str(trigger_id), "task_id": str(task_id), "spawn": result}


def fire_due() -> list[dict]:
    results = []
    now = datetime.now(timezone.utc)
    with queries.conn() as c, c.cursor() as cur:
        cur.execute(
            """
            SELECT trigger_id FROM triggers
             WHERE active
               AND trigger_type = 'cron'
               AND next_fire_at IS NOT NULL
               AND next_fire_at <= %s
            """,
            (now,),
        )
        rows = cur.fetchall()

    for r in rows:
        try:
            results.append(fire_trigger(r["trigger_id"]))
        except Exception as e:
            results.append({"trigger_id": str(r["trigger_id"]), "error": str(e)})

    return results
