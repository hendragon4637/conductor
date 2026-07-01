"""FastAPI entrypoint for evaluator-svc.

Initialises the database, declares RabbitMQ topology, and starts
event consumers for ``node.observed`` and ``ratchet.trigger``.

Usage:
    python -m services.evaluator.main
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
from pydantic import BaseModel

from contracts.events import GateEvaluated, NodeRemediate
from shared.bus import EventBus
from shared.config import ServiceConfig
from shared.db import init_db
from shared.outbox import emit

logger = logging.getLogger(__name__)
if not logger.handlers:
    logger.addHandler(logging.StreamHandler())
    logger.setLevel(logging.INFO)

# ── Globals ──────────────────────────────────────────────────────────────

cfg = ServiceConfig.from_env()
bus = EventBus(cfg)

# ── Handlers ─────────────────────────────────────────────────────────────


NON_TERMINAL_VERDICTS = {"crashed", "stalled", "quota"}


def _non_terminal_outcome(
    s, ns: "NodeSession", verdict: str,
) -> None:
    """V9: Non-terminal verdict → treat as failed attempt for patience."""
    from backend.evaluator.remediation import (
        AttemptSnapshot,
        best_score,
        should_continue,
    )

    prior_sessions: list[NodeSession] = (
        s.query(NodeSession)
        .filter(
            NodeSession.run_id == ns.run_id,
            NodeSession.node_id == ns.node_id,
            NodeSession.id != ns.id,
        )
        .order_by(NodeSession.attempt)
        .all()
    )
    history: list[AttemptSnapshot] = [
        AttemptSnapshot(
            l1_passed_ids=ps.l1_passed_ids or [],
            l2_score=ps.l2_score,
            gate_outcome=ps.gate_outcome,
        )
        for ps in prior_sessions
    ]
    history.append(AttemptSnapshot(
        l1_passed_ids=ns.l1_passed_ids or [],
        l2_score=ns.l2_score,
        gate_outcome="failed",
    ))

    continue_bool, stop_reason = should_continue(history)
    best = best_score(history)
    gate_outcome = "remediate" if continue_bool else "failed"

    ns.gate_outcome = gate_outcome
    ns.best_score = best
    ns.fail_reason = f"verdict={verdict}: {stop_reason}"

    emit(s, GateEvaluated(
        node_session_id=ns.id,
        run_id=ns.run_id,
        node_id=ns.node_id,
        gate_outcome=gate_outcome,
        l1_pass=False,
        l2_score=None,
        best_score=best,
        feedback_ref=ns.id,
        ts=time.time(),
    ))

    if gate_outcome == "remediate":
        emit(s, NodeRemediate(
            run_id=ns.run_id,
            node_id=ns.node_id,
            prev_session_id=ns.id,
            attempt_next=(ns.attempt or 1) + 1,
            feedback_ref=ns.id,
            worktree=ns.worktree or "",
            ts=time.time(),
        ))

    logger.info(
        "Non-terminal %s node_session=%s outcome=%s stop=%s",
        verdict, ns.id, gate_outcome, stop_reason,
    )
    print(  # noqa: T201
        f"[PRINT] Evaluator: node_session={ns.id} "
        f"verdict={verdict} outcome={gate_outcome} stop={stop_reason}",
        flush=True,
    )


def on_node_observed(s, payload: dict) -> None:  # noqa: C901  # noqa: PLR0912
    """Handle ``node.observed`` — run evaluator gates and emit results.

    Flow:
        1. Check verdict — non-terminal (crashed/stalled/quota) skips
           the gate and goes straight to patience (V9).
        2. Load NodeSession + node definition from DB.
        3. Build check list from the plan node.
        4. Run ``evaluate_gate`` (L1 → L2); includes false-fail escalation (V8).
        5. Apply patience-based early stopping.
        6. Persist gate results on the NodeSession.
        7. Emit ``GateEvaluated`` + optionally ``NodeRemediate``.
    """
    from backend.evaluator.gate import GateDecision, evaluate_gate
    from backend.evaluator.l2_judge import JudgeUnavailableError, run_l2
    from backend.evaluator.remediation import (
        AttemptSnapshot,
        best_score,
        build_feedback,
        should_continue,
    )
    from backend.planning.store import get_plan

    from shared.models import NodeSession, Run

    node_session_id: str = payload["node_session_id"]
    verdict: str = payload.get("verdict", "done")

    # 1. Load node session
    ns: NodeSession | None = (
        s.query(NodeSession).filter(NodeSession.id == node_session_id).first()
    )
    if ns is None:
        logger.error("NodeSession %s not found", node_session_id)
        return
    worktree: str | None = ns.worktree
    if not worktree:
        logger.error("NodeSession %s has no worktree", node_session_id)
        return

    # 1b. V9: Non-terminal verdict → skip gate, treat as failed attempt
    if verdict in NON_TERMINAL_VERDICTS:
        _non_terminal_outcome(s, ns, verdict)
        return

    # 2. Load run → plan → node definition for checks
    run_row: Run | None = (
        s.query(Run).filter(Run.id == ns.run_id).first()
    )
    if run_row is None:
        logger.error("Run %s not found for NodeSession %s", ns.run_id, node_session_id)
        return

    plan = get_plan(run_row.plan_id)
    if plan is None:
        logger.error("Plan %s not found", run_row.plan_id)
        return

    dag: list[dict[str, Any]] = plan.get("dag", [])
    node_def: dict[str, Any] | None = None
    for nd in dag:
        if nd.get("id") == ns.node_id:
            node_def = nd
            break
    if node_def is None:
        logger.error(
            "Node %s not found in plan %s DAG", ns.node_id, run_row.plan_id,
        )
        return

    check_list: list[Any] = node_def.get("checks", [])

    # 3. Run evaluator gate (includes false-fail escalation — V8)
    #    Wrapped in try/except to match monolith's error discipline:
    #    JudgeUnavailableError → loud failure (node left for human review)
    #    Generic Exception     → fail-open (emit error event, don't block)
    decision: GateDecision | None = None
    judge_error: bool = False
    gate_exc: str | None = None
    try:
        decision = evaluate_gate(
            check_list=check_list,
            worktree=worktree,
            l2_fn=lambda checks, wt: run_l2(
                checks, wt, trace_id=ns.langfuse_trace_id,
            ),
            threshold=0.7,
            prev_l1_passed_ids=ns.l1_passed_ids or None,
            has_changes_since_prev=bool(ns.remediation_of),
        )
    except JudgeUnavailableError:
        logger.error(
            "JUDGE_UNAVAILABLE for node_session=%s — all judge models unreachable",
            node_session_id,
        )
        print(  # noqa: T201
            f"[PRINT] Evaluator: JUDGE_UNAVAILABLE ns={node_session_id}",
            flush=True,
        )
        judge_error = True
        _record_judge_error(s, ns, node_session_id)
        return
    except Exception as exc:
        logger.exception(
            "Evaluator gate exception for node_session=%s", node_session_id,
        )
        print(  # noqa: T201
            f"[PRINT] Evaluator: GATE_EXCEPTION ns={node_session_id} err={exc}",
            flush=True,
        )
        gate_exc = str(exc)[:500]
        # Fall through — emit GateEvaluated with gate_outcome='error'

    # ── V8 observability ──────────────────────────────────────────────
    if decision is not None and decision.l1_flagged:
        logger.warning(
            "False-fail escalation ns=%s L1_flag=True — L2 probe passed "
            "but L1 checks still failing",
            node_session_id,
        )
        print(  # noqa: T201
            f"[PRINT] V8 false-fail ns={node_session_id} l1_flagged=True",
            flush=True,
        )

    # ── Determine outcome ────────────────────────────────────────────
    if gate_exc:
        gate_outcome = "error"
        best = None
        stop_reason = gate_exc
    elif decision is not None and decision.action == "done":
        gate_outcome = "done"
        best = decision.goal_review
        stop_reason = "passed"
    else:
        # 4. Patience / history (only for non-error outcomes)
        prior_sessions: list[NodeSession] = (
            s.query(NodeSession)
            .filter(
                NodeSession.run_id == ns.run_id,
                NodeSession.node_id == ns.node_id,
                NodeSession.id != node_session_id,
            )
            .order_by(NodeSession.attempt)
            .all()
        )
        history: list[AttemptSnapshot] = [
            AttemptSnapshot(
                l1_passed_ids=ps.l1_passed_ids or [],
                l2_score=ps.l2_score,
                gate_outcome=ps.gate_outcome,
            )
            for ps in prior_sessions
        ]
        history.append(AttemptSnapshot(
            l1_passed_ids=decision.l1_passed_ids if decision else [],
            l2_score=decision.goal_review if decision else None,
            gate_outcome=decision.action if decision else "remediate",
        ))

        continue_bool, stop_reason = should_continue(history)
        best = best_score(history)

        if not continue_bool:
            gate_outcome = "failed"
        else:
            gate_outcome = "remediate"

    # 5. Persist gate results on the NodeSession
    if decision is not None:
        ns.l1_pass = len(decision.l1_passed_ids) > 0
        ns.l1_passed_ids = decision.l1_passed_ids
        ns.l1_feedback = decision.l1_feedback
        ns.l1_flagged = decision.l1_flagged
        ns.l2_passed = decision.l2_passed
        ns.l2_score = decision.goal_review
        ns.l2_feedback = decision.l2_feedback
        ns.goal_review = decision.goal_review
    ns.gate_outcome = gate_outcome
    ns.best_score = best
    ns.fail_reason = stop_reason

    # 6. Build feedback (only when gate ran)
    if decision is not None and not gate_exc:
        feedback = build_feedback(decision)
        ns.feedback = feedback
    else:
        ns.feedback = {"error": stop_reason or gate_exc} if (gate_exc or judge_error) else None

    # 7. Emit GateEvaluated
    l2_score_val = decision.goal_review if decision else None
    gate_event = GateEvaluated(
        node_session_id=node_session_id,
        run_id=ns.run_id,
        node_id=ns.node_id,
        gate_outcome=gate_outcome,
        l1_pass=ns.l1_pass if decision else False,
        l2_score=l2_score_val,
        best_score=best,
        feedback_ref=node_session_id,
        ts=time.time(),
    )
    emit(s, gate_event)

    # 8. Emit NodeRemediate if remediation needed (not for errors)
    if gate_outcome == "remediate" and not gate_exc and not judge_error:
        attempt_next = (ns.attempt or 1) + 1
        remediate_event = NodeRemediate(
            run_id=ns.run_id,
            node_id=ns.node_id,
            prev_session_id=node_session_id,
            attempt_next=attempt_next,
            feedback_ref=node_session_id,
            worktree=worktree,
            ts=time.time(),
        )
        emit(s, remediate_event)

    logger.info(
        "Gate %s node_session=%s outcome=%s l1=%s l2=%s best=%s stop=%s",
        gate_outcome,
        node_session_id,
        ns.l1_pass if decision else "N/A",
        l2_score_val,
        best,
        stop_reason,
    )

    print(  # noqa: T201
        f"[PRINT] Evaluator: node_session={node_session_id} "
        f"outcome={gate_outcome} l1={ns.l1_pass if decision else 'N/A'} "
        f"l2={l2_score_val} best={best} stop={stop_reason}",
        flush=True,
    )


def _record_judge_error(s, ns: "NodeSession", node_session_id: str) -> None:
    """Record judge-unavailable error on the node_session.

    Mirrors monolith ``_record_judge_error()`` (supervisor.py) but uses the
    service's SQLAlchemy session instead of raw psycopg.

    Sets ``gate_outcome='judge_error'``, leaves node in current verdict
    (usually ``running``) for human review — never auto-advances.
    """
    ns.gate_outcome = "judge_error"
    ns.goal_review = None
    ns.l2_score = None
    ns.l2_passed = False
    ns.l1_pass = None
    ns.fail_reason = "All judge models unreachable — node left for human review"

    emit(s, GateEvaluated(
        node_session_id=node_session_id,
        run_id=ns.run_id,
        node_id=ns.node_id,
        gate_outcome="judge_error",
        l1_pass=None,
        l2_score=None,
        best_score=ns.best_score,
        feedback_ref=node_session_id,
        ts=time.time(),
    ))

    logger.error(
        "JUDGE_ERROR recorded for node_session=%s "
        "gate_outcome=judge_error, node left for human review",
        node_session_id,
    )
    print(  # noqa: T201
        f"[PRINT] Evaluator: JUDGE_ERROR ns={node_session_id} "
        f"recorded on node_session",
        flush=True,
    )


def on_ratchet_trigger(s, payload: dict) -> None:
    """Handle ``ratchet.trigger`` — run a ratchet experiment."""
    from backend.evaluator.ratchet import run_experiment

    agent_config_id: str = payload["agent_config_id"]
    node_type: str = payload.get("node_type", "executor")

    logger.info(
        "Ratchet trigger: agent_config=%s node_type=%s",
        agent_config_id, node_type,
    )

    try:
        result = run_experiment(
            agent_config_id=agent_config_id,
            node_type=node_type,
        )
        logger.info(
            "Experiment %s: kept=%s baseline=%s candidate=%s delta=%s",
            agent_config_id,
            result.kept,
            result.baseline_mean,
            result.candidate_mean,
            result.candidate_mean - result.baseline_mean if result.candidate_mean else 0,
        )
        print(  # noqa: T201
            f"[PRINT] Ratchet: agent={agent_config_id} "
            f"kept={result.kept} delta={result.candidate_mean - result.baseline_mean:.4f}",
            flush=True,
        )
    except Exception:
        logger.exception(
            "Ratchet experiment failed for agent_config=%s", agent_config_id,
        )


# ── Lifespan ─────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle.

    Startup:
        - Initialise the database engine.
        - Declare RabbitMQ exchange + queue topology.
        - Start event consumers on ``evaluator.q`` (one per routing key).
        - Launch the outbox relay daemon thread.

    Shutdown:
        - Close the RabbitMQ connection.
    """
    init_db(cfg)
    logger.info("DB initialised for service=%s env=%s", cfg.service, cfg.env)

    bus.declare()
    logger.info("RabbitMQ topology declared")

    # Single consumer on evaluator.q dispatches by payload shape.
    # Multiple consumers on one queue would round-robin between handlers,
    # causing node.observed events to hit the ratchet handler and vice versa.
    def _dispatch(s, payload):
        if "node_session_id" in payload:
            on_node_observed(s, payload)
        elif "agent_config_id" in payload:
            on_ratchet_trigger(s, payload)
        else:
            logger.warning("No handler for payload keys: %s", list(payload.keys()))

    bus.start_consumer(
        "evaluator.q",
        _dispatch,
        consumer_name="evaluator.dispatch",
    )
    logger.info("Consumer started on evaluator.q (dispatch)")

    relay_thread = threading.Thread(
        target=bus.relay_loop,
        daemon=True,
        name="outbox-relay",
    )
    relay_thread.start()
    logger.info("Outbox relay thread started")

    consumer_thread = threading.Thread(
        target=bus.start_consuming,
        daemon=True,
        name="eval-consumer",
    )
    consumer_thread.start()
    logger.info("Consumer pumping thread started")

    yield

    bus.close()
    logger.info("Bus connection closed")


# ── FastAPI app ──────────────────────────────────────────────────────────

app = FastAPI(title="evaluator-svc", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "service": cfg.service,
        "env": cfg.env,
    }


class CalibrateResponse(BaseModel):
    """Response from the L3 calibration endpoint."""

    node_type: str
    trusted: bool
    agreement: float
    mae: float
    total: int
    note: str


@app.post("/calibrate/{node_type}", response_model=CalibrateResponse)
def calibrate_endpoint(node_type: str) -> CalibrateResponse:
    """Run L3 calibration for a node type against the frozen golden set.

    Re-scores all frozen golden artifacts for ``node_type`` via the L2
    judge, computes MAE and item-level agreement, and returns a
    ``CalibrationReport``.  This runs out-of-band (not in the hot path).
    """
    from backend.evaluator.l3_calibrate import calibrate as run_calibrate

    report = run_calibrate(node_type)
    return CalibrateResponse(
        node_type=report.node_type,
        trusted=report.trusted,
        agreement=report.agreement,
        mae=report.mae,
        total=report.total,
        note=report.note,
    )


# ── Main ─────────────────────────────────────────────────────────────────


def main() -> None:
    """Start the evaluator-svc uvicorn server.

    Port sourced from ``EVALUATOR_PORT`` env var (default ``8093``).
    """
    port = int(os.environ.get("EVALUATOR_PORT", "8093"))
    uvicorn.run(
        "services.evaluator.main:app",
        host="0.0.0.0",
        port=port,
        log_level=os.environ.get("LOG_LEVEL", "info").lower(),
    )


if __name__ == "__main__":
    main()
