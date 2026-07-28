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

import uvicorn
from fastapi import FastAPI

from contracts.events import (
    L4Findings, PlanRatifiable, PlanFailed as PlanFailedEvent,
    PlanRejected, PlanAwaitingClarification, RunFailed,
)
from shared.bus import EventBus
from shared.config import ServiceConfig
from shared.db import init_db

from services.intake.store import (
    insert_intent, update_intent, load_intent_by_plan,
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
            return (row[0] or 0) >= RATE_LIMIT_PER_HOUR


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


def _post_goal(intent: dict) -> dict:
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
    resp = httpx.post(f"{planner_url}/goal", json=payload, timeout=120.0)
    resp.raise_for_status()
    return resp.json()


def _post_clarify(plan_id: str, answer: str) -> dict:
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


def _submit(intent: dict) -> None:
    """Normalize, dedupe, check guards, then submit to planner.

    Writes the intent to DB first so the row has an id for intake_id.
    """
    project_id = intent["project_id"]

    if _paused(project_id):
        insert_intent(intent, status="proposed")
        logger.info("Intake paused for %s — intent stored as proposed", project_id)
        return

    if is_duplicate(intent, window_days=DEDUPE_WINDOW_DAYS):
        insert_intent(intent, status="duplicate")
        logger.info("Duplicate intent for %s source_ref=%s", project_id, intent.get("source_ref"))
        return

    if _over_rate_limit(project_id):
        insert_intent(intent, status="escalated", last_error="rate limit")
        logger.warning("Rate limit hit for %s — intent escalated", project_id)
        return

    if not _project_free(project_id):
        insert_intent(intent, status="proposed")
        logger.info("Project %s busy — intent stored as proposed", project_id)
        return

    row = insert_intent(intent, status="submitted")
    try:
        resp = _post_goal(row)
        plan_id = resp.get("plan_id", "")
        if plan_id:
            update_intent(row["id"], plan_id=plan_id)
    except Exception as exc:
        logger.exception("POST /goal failed for intent_id=%s", row["id"])
        update_intent(row["id"], status="escalated", last_error=str(exc)[:500])


# ── Event handlers ───────────────────────────────────────────────────────


def on_run_failed(s, payload: dict) -> None:
    """Handle run.failed — create an improvement intent."""
    from services.intake.adapters.run_failed import RunFailedAdapter
    adapter = RunFailedAdapter()
    for intent in adapter.normalize(payload):
        _submit(intent.model_dump())


def on_l4_findings(s, payload: dict) -> None:
    """Handle l4.findings — create an improvement intent from L4 report."""
    from services.intake.adapters.l4_findings import L4FindingsAdapter
    adapter = L4FindingsAdapter()
    for intent in adapter.normalize(payload):
        _submit(intent.model_dump())


def on_human_feedback(body: dict) -> None:
    """Handle POST /intake/feedback — create an improvement intent."""
    from services.intake.adapters.human_feedback import HumanFeedbackAdapter
    adapter = HumanFeedbackAdapter()
    for intent in adapter.normalize(body):
        _submit(intent.model_dump())


def on_clarification_needed(s, payload: dict) -> None:
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
    _post_clarify(payload["plan_id"], ans.text if hasattr(ans, "text") else str(ans))


def on_plan_ratifiable(s, payload: dict) -> None:
    """Handle plan.ratifiable — auto-ratify or park for human."""
    row = load_intent_by_plan(payload["plan_id"])
    if not row:
        return
    if not AUTO_RATIFY:
        update_intent(row["id"], status="awaiting_ratify")
        return
    _post_ratify(payload["plan_id"])
    update_intent(row["id"], status="running")


def on_plan_failed(s, payload: dict) -> None:
    """Handle plan.failed — reformulate via plan_failed adapter."""
    _reformulate("plan_failed", payload, payload.get("error", "gate failure"))


def on_plan_rejected(s, payload: dict) -> None:
    """Handle plan.rejected — reformulate via ratify_rejected adapter."""
    _reformulate("ratify_rejected", payload, payload.get("reason", "rejected"))


def _reformulate(origin: str, payload: dict, note: str) -> None:
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

    _MAP = {
        "run_failed": RunFailedAdapter,
        "l4_findings": L4FindingsAdapter,
        "plan_failed": PlanFailedAdapter,
        "ratify_rejected": RatifyRejectedAdapter,
        "human_feedback": HumanFeedbackAdapter,
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


# ── Lifespan ─────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle."""
    init_db(cfg)
    logger.info("DB initialised for service=%s env=%s", cfg.service, cfg.env)

    bus.declare()
    logger.info("RabbitMQ topology declared")

    # Single dispatcher on intake.q
    def _dispatch(s, payload):
        if not INTAKE_ENABLED:
            logger.info("INTAKE_ENABLED=false — dropping event: %s", list(payload.keys())[:3])
            return
        if "run_id" in payload and "plan_id" in payload and "findings" in payload:
            on_l4_findings(s, payload)
        elif "questions" in payload:
            on_clarification_needed(s, payload)
        elif "error" in payload and "plan_id" in payload and "project_id" in payload:
            on_plan_failed(s, payload)
        elif "reason" in payload and "rejected_by" in payload:
            on_plan_rejected(s, payload)
        elif "plan_id" in payload and "project_id" in payload:
            on_plan_ratifiable(s, payload)
        elif payload.get("event_type") == "run.failed":
            on_run_failed(s, payload)
        else:
            logger.warning("No intake handler for payload keys: %s", list(payload.keys()))

        # After any event that might free a project, try to drain
        project_id = payload.get("project_id", "")
        if project_id:
            _drain_proposed(project_id)

    bus.start_consumer("intake.q", _dispatch, "intake.dispatch")
    logger.info("Consumer started on intake.q (dispatch)")

    relay_thread = threading.Thread(target=bus.relay_loop, daemon=True, name="outbox-relay")
    relay_thread.start()
    logger.info("Outbox relay thread started")

    consumer_thread = threading.Thread(target=bus.start_consuming, daemon=True, name="intake-consumer")
    consumer_thread.start()
    logger.info("Consumer pumping thread started")

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
    findings: list[dict] = []  # [{what, where[], why}]


@app.post("/intake/feedback")
def receive_feedback(body: FeedbackRequest):
    """Accept human feedback and submit as an improvement goal."""
    on_human_feedback(body.model_dump())
    _drain_proposed(body.project_id)
    return {"status": "accepted", "project_id": body.project_id}


# ── Observability endpoint ───────────────────────────────────────────────


class IntentsQuery(BaseModel):
    project_id: str | None = None
    status: str | None = None


@app.get("/intake/intents")
def list_intents(project_id: str | None = None, status: str | None = None):
    """List intake_intents with optional filters."""
    rows = query_intents(project_id=project_id, status=status)
    return {"intents": rows}


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
