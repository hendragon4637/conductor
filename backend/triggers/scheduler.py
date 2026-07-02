from __future__ import annotations

import json
import os
import uuid
from typing import Any, Callable

import psycopg
from psycopg.rows import dict_row

from backend.triggers.guardrails import with_guardrails
from backend.triggers.jobs import run_task, enrich, ratchet_sweep, calibrate_sweep


_JOB_REGISTRY: dict[str, Callable] = {
    "run_task": run_task,
    "enrich": enrich,
    "ratchet_sweep": ratchet_sweep,
    "calibrate_sweep": calibrate_sweep,
}


def _get_db() -> str:
    return os.environ["DATABASE_URL"]


def _to_uuid(tid: str) -> str:
    raw = uuid.uuid5(uuid.NAMESPACE_DNS, tid).hex
    return f"{raw[:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:32]}"


class Scheduler:
    """Cron-style scheduler backed by APScheduler with DB persistence."""

    def __init__(self):
        self._aps = None

    def _ensure_aps(self):
        if self._aps is None:
            from apscheduler.schedulers.background import BackgroundScheduler
            self._aps = BackgroundScheduler(daemon=True)

    def load_from_db(self) -> list[dict[str, Any]]:
        """Load all active cron triggers from the database and schedule them."""
        self._ensure_aps()
        with psycopg.connect(_get_db(), row_factory=dict_row) as c:
            with c.cursor() as cur:
                cur.execute(
                    """SELECT * FROM triggers
                       WHERE active AND trigger_type = 'cron'
                       ORDER BY created_at"""
                )
                triggers = cur.fetchall()

        for t in triggers:
            self._schedule_one(t)

        if self._aps and not self._aps.running:
            self._aps.start()

        return triggers

    def _schedule_one(self, trigger: dict[str, Any]) -> None:
        self._ensure_aps()
        tid = str(trigger["trigger_id"])
        cron_expr = trigger.get("cron_expression", "")
        if not cron_expr:
            return

        def _fire():
            return self.fire(tid)

        try:
            self._aps.add_job(
                _fire,
                trigger="cron",
                cron=cron_expr,
                id=tid,
                replace_existing=True,
                name=trigger.get("name", tid),
            )
        except Exception:
            pass

    def add(
        self,
        name: str,
        cron: str,
        job_type: str,
        payload: dict[str, Any] | None = None,
        project_id: str = "default",
        session_id: str = "default",
        agent_config_id: str = "opencode:backend-executor",
        sandboxed: bool = True,
    ) -> str:
        """Add a cron trigger and persist it to the database."""
        trigger_id = _to_uuid(f"trg-auto-{uuid.uuid4().hex[:12]}")

        intent_template = payload.get("intent", name) if payload else name
        input_override = json.dumps({
            "job_type": job_type,
            "payload": payload or {},
            "sandboxed": sandboxed,
        })

        with psycopg.connect(_get_db()) as c:
            with c.cursor() as cur:
                cur.execute(
                    """INSERT INTO triggers
                       (trigger_id, name, trigger_type, project_id, session_id,
                        agent_config_id, cron_expression, intent_template,
                        input_spec_override, sandboxed, job_type, active)
                       VALUES (%s::uuid, %s, 'cron', %s, %s, %s, %s, %s,
                               %s::jsonb, %s, %s, true)
                       ON CONFLICT (trigger_id) DO NOTHING
                    """,
                    (trigger_id, name, project_id, session_id, agent_config_id,
                     cron, intent_template, input_override, sandboxed, job_type),
                )
            c.commit()

        return str(trigger_id)

    def fire(self, trigger_id: str) -> dict[str, Any]:
        """Fire a trigger immediately, wrapped in guardrails."""
        with psycopg.connect(_get_db(), row_factory=dict_row) as c:
            with c.cursor() as cur:
                cur.execute(
                    "SELECT * FROM triggers WHERE trigger_id = %s::uuid",
                    (trigger_id,),
                )
                trigger = cur.fetchone()

        if trigger is None:
            return {"status": "error", "message": "trigger not found"}

        input_override = trigger.get("input_spec_override", {})
        if isinstance(input_override, str):
            input_override = json.loads(input_override)

        payload = {"intent": trigger.get("intent_template", "")}
        if isinstance(input_override, dict):
            job_payload = input_override.get("payload", {})
            if isinstance(job_payload, dict):
                payload.update(job_payload)
            payload["_sandboxed"] = input_override.get(
                "sandboxed", trigger.get("sandboxed", True)
            )
            payload["_job_type"] = input_override.get(
                "job_type", trigger.get("job_type", "")
            )

        job_type = payload.pop("_job_type", None) or trigger.get("job_type", "enrich")
        fn = _JOB_REGISTRY.get(job_type)
        if fn is None:
            return {"status": "error", "message": f"unknown job_type: {job_type}"}

        with psycopg.connect(_get_db()) as c:
            with c.cursor() as cur:
                cur.execute(
                    "UPDATE triggers SET fire_count = fire_count + 1, "
                    "last_fired_at = now() WHERE trigger_id = %s::uuid",
                    (trigger_id,),
                )
            c.commit()

        return with_guardrails(
            fn,
            {
                "payload": payload,
                "sandboxed": payload.get("_sandboxed", True),
                "trigger_type": "cron",
            },
        )

    def list_triggers(self) -> list[dict[str, Any]]:
        """Return all triggers from the database."""
        with psycopg.connect(_get_db(), row_factory=dict_row) as c:
            with c.cursor() as cur:
                cur.execute("SELECT * FROM triggers ORDER BY created_at")
                return cur.fetchall()

    def load_trigger(self, trigger_id: str) -> dict[str, Any] | None:
        """Load a single trigger by ID."""
        with psycopg.connect(_get_db(), row_factory=dict_row) as c:
            with c.cursor() as cur:
                cur.execute(
                    "SELECT * FROM triggers WHERE trigger_id = %s::uuid",
                    (trigger_id,),
                )
                return cur.fetchone()
