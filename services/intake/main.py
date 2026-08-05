"""FastAPI entrypoint for intake-svc.

Initialises the database, declares RabbitMQ topology, and starts
event consumers for intake.q.

Usage:
    python -m services.intake.main
"""

from __future__ import annotations

import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from contracts.events import (
    L4Findings, PlanRatifiable, PlanFailed as PlanFailedEvent,
    PlanRejected, PlanAwaitingClarification, RunFailed,
)
from shared.bus import EventBus
from shared.config import ServiceConfig
from shared.db import init_db

from services.intake.store import (
    insert_intent, update_intent, load_intent_by_plan, load_intent_by_id,
    oldest_proposed, query_intents, is_duplicate,
)

logger = logging.getLogger(__name__)
if not logger.handlers:
    logger.addHandler(logging.StreamHandler())
    logger.setLevel(logging.INFO)

# ── Globals ──────────────────────────────────────────────────────────────

cfg = ServiceConfig.from_env()
bus = EventBus(cfg)

# ── Configurable limits ───────────────────────────────────────────────────

AUTO_RATIFY = os.environ.get("AUTO_RATIFY", "false").lower() == "true"
INTAKE_ENABLED = os.environ.get("INTAKE_ENABLED", "true").lower() == "true"
DEDUPE_WINDOW_DAYS = int(os.environ.get("DEDUPE_WINDOW_DAYS", "7"))
MAX_CLARIFY_ROUNDS = int(os.environ.get("MAX_CLARIFY_ROUNDS", "3"))
RATE_LIMIT_PER_HOUR = int(os.environ.get("RATE_LIMIT_PER_HOUR", "3"))
STALE_SWEEP_HOURS = int(os.environ.get("STALE_SWEEP_HOURS", "24"))
STALE_SWEEP_INTERVAL_MINUTES = int(os.environ.get("STALE_SWEEP_INTERVAL_MINUTES", "60"))


# ── Helpers ──────────────────────────────────────────────────────────────


def _project_free(project_id: str) -> bool:
    """Check if project has no active run (non-terminal)."""
    from backend.planning.store import get_active_run_for_project
    return get_active_run_for_project(project_id) is None


def _paused(project_id: str) -> bool:
    """Check if project intake is paused."""
    import psycopg
    db_url = os.environ["DATABASE_URL"]
    with psycopg.connect(db_url) as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT intake_paused FROM project_flags WHERE project_id = %s",
                (project_id,),
            )
            row = cur.fetchone()
            return bool(row[0]) if row else False


def _over_rate_limit(project_id: str) -> bool:
    """Check if project has exceeded auto-goals per hour."""
    import psycopg
    db_url = os.environ["DATABASE_URL"]
    with psycopg.connect(db_url) as c:
        with c.cursor() as cur:
            cur.execute(
                """SELECT count(*) FROM intake_intents
                   WHERE project_id = %s
                     AND created_at > now() - interval '1 hour'
                     AND origin != 'human_feedback'""",
                (project_id,),
            )
            row = cur.fetchone()
            return (row[0] if row else 0) >= RATE_LIMIT_PER_HOUR


def _escalate(row, reason: str) -> None:
    """Mark an intent as escalated (terminal) and log."""
    import psycopg
    db_url = os.environ["DATABASE_URL"]
    with psycopg.connect(db_url) as c:
        with c.cursor() as cur:
            cur.execute(
                """UPDATE intake_intents
                   SET status = 'escalated', last_error = %s, updated_at = now()
                   WHERE id = %s""",
                (reason, row["id"]),
            )
        c.commit()
    logger.warning(
        "Escalated intent_id=%s project=%s origin=%s attempt=%s reason=%s",
        row["id"], row["project_id"], row["origin"], row["attempt"], reason,
    )


def _post_goal(intent: dict[str, Any]) -> dict[str, Any]:
    """POST a normalized goal to planner-svc /goal.

    Returns the response dict with 'plan_id' and 'status'.
    """
    import httpx
    planner_url = os.environ.get("PLANNER_URL", "http://127.0.0.1:8093")
    payload = {
        "raw_input": intent["intent_text"],
        "origin": intent["origin"],
        "source_ref": intent.get("source_ref"),
        "intake_id": intent["id"],
        "evidence": intent.get("evidence", []),
        "project_id": intent["project_id"],
    }
    if intent.get("spec"):
        payload["spec"] = intent["spec"]
    if intent.get("quality_intent"):
        payload["quality_intent"] = intent["quality_intent"]
    resp = httpx.post(f"{planner_url}/goal", json=payload, timeout=120.0)
    resp.raise_for_status()
    return resp.json()


def _post_clarify(plan_id: str, answer: str) -> dict[str, Any]:
    """POST a clarification answer to planner-svc."""
    import httpx
    planner_url = os.environ.get("PLANNER_URL", "http://127.0.0.1:8093")
    resp = httpx.post(
        f"{planner_url}/clarify/{plan_id}",
        json={"answer": answer},
        timeout=120.0,
    )
    resp.raise_for_status()
    return resp.json()


def _post_ratify(plan_id: str) -> None:
    """POST /ratify to planner-svc — fire and forget.

    Response is intentionally ignored; planner emits the authoritative event.
    """
    import httpx
    planner_url = os.environ.get("PLANNER_URL", "http://127.0.0.1:8093")
    try:
        httpx.post(f"{planner_url}/ratify/{plan_id}", timeout=30.0)
    except Exception:
        logger.warning("Ratify call for %s failed — awaiting planner event", plan_id)


# ── Submit (shared by all entry handlers) ────────────────────────────────


def _submit(intent: dict[str, Any]) -> str | None:
    """Normalize, dedupe, check guards, then submit to planner.

    Writes the intent to DB first so the row has an id for intake_id.

    Returns:
        Plan ID on successful submission, ``None`` otherwise.
    """
    project_id = intent["project_id"]

    if _paused(project_id):
        insert_intent(intent, status="proposed")
        logger.info("Intake paused for %s — intent stored as proposed", project_id)
        return None

    if is_duplicate(intent, window_days=DEDUPE_WINDOW_DAYS):
        insert_intent(intent, status="duplicate")
        logger.info("Duplicate intent for %s source_ref=%s", project_id, intent.get("source_ref"))
        return None

    if _over_rate_limit(project_id):
        insert_intent(intent, status="escalated", last_error="rate limit")
        logger.warning("Rate limit hit for %s — intent escalated", project_id)
        return None

    if not _project_free(project_id):
        insert_intent(intent, status="proposed")
        logger.info("Project %s busy — intent stored as proposed", project_id)
        return None

    row = insert_intent(intent, status="submitted")
    if intent.get("spec"):
        row["spec"] = intent["spec"]
    if intent.get("quality_intent"):
        row["quality_intent"] = intent["quality_intent"]
    try:
        resp = _post_goal(row)
        plan_id = resp.get("plan_id", "")
        if plan_id:
            update_intent(row["id"], plan_id=plan_id)
        return plan_id
    except Exception as exc:
        logger.exception("POST /goal failed for intent_id=%s", row["id"])
        update_intent(row["id"], status="escalated", last_error=str(exc)[:500])
        return None


# ── Event handlers ───────────────────────────────────────────────────────


def on_run_failed(s, payload: dict[str, Any]) -> None:
    """Handle run.failed — create an improvement intent."""
    from services.intake.adapters.run_failed import RunFailedAdapter
    adapter = RunFailedAdapter()
    for intent in adapter.normalize(payload):
        _submit(intent.model_dump())


def on_l4_findings(s, payload: dict[str, Any]) -> None:
    """Handle l4.findings — create an improvement intent from L4 report."""
    from services.intake.adapters.l4_findings import L4FindingsAdapter
    adapter = L4FindingsAdapter()
    for intent in adapter.normalize(payload):
        _submit(intent.model_dump())


def on_system_goal_queued(s, payload: dict[str, Any]) -> None:
    """Consume ``sys.goal_queued`` — create intent, submit to planner."""
    import psycopg
    db_url = os.environ["DATABASE_URL"]
    project_id = payload.get("project_id", "")
    raw_input = payload.get("raw_input", "")

    if not project_id or not raw_input:
        logger.warning("sys.goal_queued missing project_id or raw_input: %s", payload)
        return

    from services.intake.adapters.system_goal import SystemGoalAdapter
    intents = SystemGoalAdapter().normalize(payload)
    plan_id = _submit(intents[0].model_dump()) if intents else None

    try:
        with psycopg.connect(db_url) as c:
            with c.cursor() as cur:
                if plan_id:
                    cur.execute(
                        """UPDATE pending_goals
                           SET status = 'submitted', plan_id = %s, updated_at = now()
                           WHERE project_id = %s AND status = 'in_progress'""",
                        (plan_id, project_id),
                    )
                else:
                    cur.execute(
                        """UPDATE pending_goals
                           SET last_error = 'intake submission failed', updated_at = now()
                           WHERE project_id = %s AND status = 'in_progress'""",
                        (project_id,),
                    )
            c.commit()
    except Exception as exc:
        logger.exception("Failed to update pending_goals for %s", project_id)


def on_run_merged(s, payload: dict[str, Any]) -> None:
    """Handle run.merged — drain pending goals for the merged project."""
    from services.planner.system_goal import drain_pending
    project_id = payload.get("project_id", "")
    if project_id:
        logger.info("Run merged for %s — draining pending goals", project_id)
    try:
        count = drain_pending()
        if count:
            logger.info("Drained %d pending goals after run.merged", count)
    except Exception as exc:
        logger.warning("drain_pending failed on run.merged: %s", exc)


def on_human_feedback(body: dict[str, Any]) -> None:
    """Handle POST /intake/feedback — create an improvement intent."""
    from services.intake.adapters.human_feedback import HumanFeedbackAdapter
    adapter = HumanFeedbackAdapter()
    for intent in adapter.normalize(body):
        _submit(intent.model_dump())


def on_clarification_needed(s, payload: dict[str, Any]) -> None:
    """Handle plan.awaiting_clarification — answer via adapter or escalate."""
    row = load_intent_by_plan(payload["plan_id"])
    if not row:
        return  # human-originated plan — not ours, ignore
    if (row.get("clarify_rounds") or 0) >= MAX_CLARIFY_ROUNDS:
        _escalate(row, "clarification rounds exhausted")
        return
    adapter_cls = _adapter_for_origin(row["origin"])
    ans = adapter_cls().answer(payload.get("questions", ""), row.get("source_ref", ""))
    if hasattr(ans, "kind") and ans.kind == "defer":
        _escalate(row, f"clarification deferred: {ans.text}")
        return
    update_intent(row["id"], status="clarifying", clarify_rounds=(row["clarify_rounds"] or 0) + 1)
    _post_clarify(payload["plan_id"], (ans.text or "") if hasattr(ans, "text") else str(ans))


def on_plan_ratifiable(s, payload: dict[str, Any]) -> None:
    """Handle plan.ratifiable — auto-ratify or park for human."""
    row = load_intent_by_plan(payload["plan_id"])
    if not row:
        return
    if not AUTO_RATIFY:
        update_intent(row["id"], status="awaiting_ratify")
        return
    _post_ratify(payload["plan_id"])
    update_intent(row["id"], status="running")


def on_plan_failed(s, payload: dict[str, Any]) -> None:
    """Handle plan.failed — reformulate via plan_failed adapter."""
    _reformulate("plan_failed", payload, payload.get("error", "gate failure"))


def on_plan_rejected(s, payload: dict[str, Any]) -> None:
    """Handle plan.rejected — reformulate via ratify_rejected adapter."""
    _reformulate("ratify_rejected", payload, payload.get("reason", "rejected"))


def _reformulate(origin: str, payload: dict[str, Any], note: str) -> None:
    """Reformulate an intent after plan failure or rejection.

    Preserves original source_ref for lineage. Caps differ by origin.
    """
    row = load_intent_by_plan(payload["plan_id"])
    if not row:
        return  # human-originated plan — not ours
    update_intent(row["id"], status="superseded", last_error=note)

    adapter_cls = _adapter_for_origin(origin)
    adapter = adapter_cls()
    if row.get("attempt", 1) >= adapter.max_attempts:
        _escalate(row, f"max attempts ({adapter.max_attempts}) reached: {note}")
        return

    new_intent = adapter.normalize({
        "plan_id": payload["plan_id"],
        "error": note,
        "reason": note,
        "rejected_by": payload.get("rejected_by", "policy"),
    })[0]
    new_intent_dict = new_intent.model_dump()
    # Carry forward the original source_ref for dedupe lineage
    if not new_intent_dict.get("source_ref"):
        new_intent_dict["source_ref"] = row.get("source_ref")
    new_intent_dict["attempt"] = (row["attempt"] or 1) + 1
    _submit(new_intent_dict)


def _adapter_for_origin(origin: str):
    """Return adapter class for the given origin string."""
    from services.intake.adapters.run_failed import RunFailedAdapter
    from services.intake.adapters.l4_findings import L4FindingsAdapter
    from services.intake.adapters.plan_failed import PlanFailedAdapter
    from services.intake.adapters.ratify_rejected import RatifyRejectedAdapter
    from services.intake.adapters.human_feedback import HumanFeedbackAdapter
    from services.intake.adapters.system_goal import SystemGoalAdapter

    _MAP = {
        "run_failed": RunFailedAdapter,
        "l4_findings": L4FindingsAdapter,
        "plan_failed": PlanFailedAdapter,
        "ratify_rejected": RatifyRejectedAdapter,
        "human_feedback": HumanFeedbackAdapter,
        "system_goal": SystemGoalAdapter,
    }
    cls = _MAP.get(origin)
    if cls is None:
        raise ValueError(f"Unknown origin: {origin}")
    return cls


def _drain_proposed(project_id: str) -> None:
    """Submit the oldest proposed intent for a project after it frees up."""
    if _paused(project_id) or not _project_free(project_id):
        return
    row = oldest_proposed(project_id)
    if row:
        logger.info("Draining proposed intent %s for project %s", row["id"], project_id)
        _submit(row)


def run_stale_sweep(max_age_hours: int | None = None) -> int:
    """Escalate submitted/clarifying intents idle past the age cap.

    Guide 08.6: a firing sweep is a bug signal — if the planner always
    emits a terminal event, nothing should be stale. Logged per row.
    """
    from services.intake.store import sweep_stale_intents

    hours = max_age_hours if max_age_hours is not None else STALE_SWEEP_HOURS
    rows = sweep_stale_intents(max_age_hours=hours)
    for row in rows:
        logger.warning(
            "Stale sweep escalated intent_id=%s project=%s origin=%s "
            "(idle > %sh)",
            row["id"], row["project_id"], row["origin"], hours,
        )
    return len(rows)


def stale_sweep_loop() -> None:
    """Background loop — run the stale sweep every interval."""
    while True:
        try:
            run_stale_sweep()
        except Exception:
            logger.exception("Stale sweep failed")
        time.sleep(STALE_SWEEP_INTERVAL_MINUTES * 60)


# ── Dispatcher — extracted to module level for testability ────────────────


def _dispatch(s, payload):
    """Route an inbound RabbitMQ event to the correct handler."""
    if not INTAKE_ENABLED:
        logger.info("INTAKE_ENABLED=false — dropping event: %s", list(payload.keys())[:3])
        return
    if "run_id" in payload and "plan_id" in payload and "findings" in payload:
        on_l4_findings(s, payload)
    elif payload.get("event_type") == "run.failed":
        on_run_failed(s, payload)
    elif "merge_commit" in payload:
        on_run_merged(s, payload)
    elif "raw_input" in payload and "project_id" in payload and "questions" not in payload:
        on_system_goal_queued(s, payload)
    elif "questions" in payload:
        on_clarification_needed(s, payload)
    elif "error" in payload and "plan_id" in payload and "project_id" in payload:
        on_plan_failed(s, payload)
    elif "reason" in payload and "rejected_by" in payload:
        on_plan_rejected(s, payload)
    elif "plan_id" in payload and "project_id" in payload:
        on_plan_ratifiable(s, payload)
    else:
        logger.warning("No intake handler for payload keys: %s", list(payload.keys()))

    # After any event that might free a project, try to drain
    project_id = payload.get("project_id", "")
    if project_id:
        _drain_proposed(project_id)


# ── Lifespan ─────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle."""
    init_db(cfg)
    logger.info("DB initialised for service=%s env=%s", cfg.service, cfg.env)

    bus.declare()
    logger.info("RabbitMQ topology declared")

    bus.start_consumer("intake.q", _dispatch, "intake.dispatch")
    logger.info("Consumer started on intake.q (dispatch)")

    relay_thread = threading.Thread(target=bus.relay_loop, daemon=True, name="outbox-relay")
    relay_thread.start()
    logger.info("Outbox relay thread started")

    consumer_thread = threading.Thread(target=bus.start_consuming, daemon=True, name="intake-consumer")
    consumer_thread.start()
    logger.info("Consumer pumping thread started")

    sweep_thread = threading.Thread(target=stale_sweep_loop, daemon=True, name="intake-stale-sweep")
    sweep_thread.start()
    logger.info("Stale sweep thread started (every %s min)", STALE_SWEEP_INTERVAL_MINUTES)

    yield

    bus.close()
    logger.info("Bus connection closed")


# ── FastAPI app ──────────────────────────────────────────────────────────

app = FastAPI(title="intake-svc", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "service": cfg.service,
        "env": cfg.env,
    }


# ── Human feedback endpoint ──────────────────────────────────────────────


from pydantic import BaseModel


class FeedbackRequest(BaseModel):
    project_id: str
    findings: list[dict[str, Any]] = []  # [{what, where[], why}]


@app.post("/intake/feedback")
def receive_feedback(body: FeedbackRequest):
    """Accept human feedback and submit as an improvement goal."""
    on_human_feedback(body.model_dump())
    _drain_proposed(body.project_id)
    return {"status": "accepted", "project_id": body.project_id}


@app.post("/intake/sweep")
def trigger_stale_sweep(max_age_hours: int | None = None):
    """Manually run the stale-intent sweep (guide 08.6)."""
    escalated = run_stale_sweep(max_age_hours=max_age_hours)
    return {"status": "ok", "escalated": escalated}


# ── Observability endpoint ───────────────────────────────────────────────


class IntentsQuery(BaseModel):
    project_id: str | None = None
    status: str | None = None


@app.get("/intake/intents")
def list_intents(project_id: str | None = None, status: str | None = None):
    """List intake_intents with optional filters."""
    rows = query_intents(project_id=project_id, status=status)
    return {"intents": rows}


class RatifyIntentRequest(BaseModel):
    auto_submit: bool = True
    """If True (default), immediately submit the ratified intent as a goal."""


@app.post("/intake/intents/{intent_id}/ratify")
def ratify_intent(intent_id: int, body: RatifyIntentRequest = RatifyIntentRequest()):
    """Ratify a proposed_project intent, creating a project row.

    Reads the ``proposed_project`` JSONB from the intent, creates a project
    in the ``projects`` table with the specified name/kind/system_id, and
    marks the intent as ``ratified``.  If ``auto_submit`` is True, the intent
    is also submitted to the planner as a goal.
    """
    import psycopg
    import json as _json

    row = load_intent_by_id(intent_id)
    if not row:
        return JSONResponse(status_code=404, content={"error": f"intent {intent_id} not found"})

    if row.get("status") not in ("proposed", "awaiting_ratify"):
        return JSONResponse(
            status_code=409,
            content={
                "error": f"intent {intent_id} has status '{row['status']}' — "
                f"expected 'proposed' or 'awaiting_ratify'",
            },
        )

    proposed = row.get("proposed_project")
    if not proposed:
        return JSONResponse(
            status_code=400,
            content={"error": f"intent {intent_id} has no proposed_project"},
        )

    pp = proposed
    if isinstance(pp, str):
        pp = _json.loads(pp)

    project_name = pp.get("project_name", f"proposal-{intent_id}")
    kind = pp.get("kind", "service")
    system_id = pp.get("system_id", row.get("project_id", "default"))

    # Generate a stable project_id from the name
    import re
    project_id = re.sub(r"[^a-z0-9_]", "_", project_name.lower().replace(" ", "_"))
    project_id = re.sub(r"_+", "_", project_id).strip("_")[:63]

    db_url = os.environ["DATABASE_URL"]
    try:
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                # Create the project row (idempotent)
                cur.execute(
                    """INSERT INTO projects (project_id, name, kind, system_id)
                       VALUES (%s, %s, %s, %s)
                       ON CONFLICT (project_id) DO UPDATE
                       SET name = EXCLUDED.name,
                           kind = EXCLUDED.kind,
                           system_id = EXCLUDED.system_id""",
                    (project_id, project_name, kind, system_id),
                )

                # Mark the intent as ratified
                cur.execute(
                    """UPDATE intake_intents
                       SET status = 'ratified', updated_at = now()
                       WHERE id = %s""",
                    (intent_id,),
                )
            conn.commit()

        # Create dependency edges (same-system, post-commit — add_dependency
        # opens its own connection)
        for dep_id in pp.get("depends_on", []) or []:
            try:
                from shared.models import add_dependency
                add_dependency(project_id, dep_id)
            except Exception as exc:
                logger.warning(
                    "Failed to add dependency %s → %s: %s", project_id, dep_id, exc,
                )
    except Exception as exc:
        logger.exception("Failed to ratify intent %d", intent_id)
        return JSONResponse(status_code=500, content={"error": str(exc)})

    logger.info(
        "Ratified intent %d → project %s (name=%s kind=%s system=%s)",
        intent_id, project_id, project_name, kind, system_id,
    )

    result = {
        "status": "ratified",
        "project_id": project_id,
        "project_name": project_name,
        "kind": kind,
        "system_id": system_id,
        "intent_id": intent_id,
    }

    # Optionally submit as a goal
    if body.auto_submit:
        try:
            row["project_id"] = project_id
            row["intent_text"] = row.get("intent_text") or (
                f"Create the {kind} project \"{project_name}\" as part of system {system_id}"
            )
            if pp.get("spec"):
                row["spec"] = pp["spec"]
            if pp.get("quality_intent"):
                row["quality_intent"] = pp["quality_intent"]
            _submit(row)
            result["submitted"] = True
        except Exception as exc:
            logger.warning("Auto-submit failed for ratified intent %d: %s", intent_id, exc)
            result["submitted"] = False
            result["submit_error"] = str(exc)[:200]

    return result


# ── Main ─────────────────────────────────────────────────────────────────


def main() -> None:
    port = int(os.environ.get("PORT", "8095"))
    uvicorn.run(
        "services.intake.main:app",
        host="0.0.0.0",
        port=port,
        log_level=os.environ.get("LOG_LEVEL", "info").lower(),
    )


if __name__ == "__main__":
    main()
