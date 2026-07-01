"""executor-svc entrypoint.

FastAPI endpoints and background consumers for plan execution,
node remediation, and gate evaluation.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI

from shared.bus import EventBus
from shared.config import ServiceConfig
from shared.db import init_db

logger = logging.getLogger(__name__)

cfg = ServiceConfig.from_env()
bus = EventBus(cfg)

# Resolve infra addresses once at module level (can be overridden via env)
_AIONUI_HOST = os.environ.get("AIONUI_HOST", "http://127.0.0.1:40937")
_WORKSPACE_ROOT = os.environ.get("WORKSPACE_ROOT", "/opt/aipc/conductor/workspace")


# ── Event handlers ──────────────────────────────────────────────────────────


def _handle_plan_ratified(session, payload):
    """Dispatch the first wave of nodes when a plan is ratified.

    Fetches the plan from the DB and delegates to the backend
    orchestration runner to launch the run.
    """
    from backend.orchestration.runner import launch_run
    from backend.planning.store import get_plan
    from shared.db import session as db_session
    from shared.models import NodeSession as NodeSessionModel

    run_id = payload["run_id"]
    plan_id = payload["plan_id"]

    plan = get_plan(plan_id)
    if not plan:
        logger.error("Plan %s not found — cannot dispatch", plan_id)
        return

    run_row = {
        "id": run_id,
        "plan_id": plan_id,
        "state": "dispatched",
    }

    logger.info("Launching run %s from plan %s", run_id, plan_id)
    launch_run(
        run_id=run_id,
        run_row=run_row,
        plan_data=plan,
        aionui_host=_AIONUI_HOST,
        workspace_root=_WORKSPACE_ROOT,
    )

    # save_node_session UPSERT omits worktree — patch directly
    worktree_path = plan.get("worktree_path")
    if worktree_path:
        with db_session() as s:
            count = (
                s.query(NodeSessionModel)
                .filter(NodeSessionModel.run_id == run_id)
                .update({"worktree": worktree_path})
            )
            s.commit()
            logger.info("Patched worktree %r on %d node_sessions for run %s", worktree_path, count, run_id)

    # Emit NodeSpawned for each node session so watcher-svc can track them
    from contracts.events import NodeSpawned
    from shared.outbox import emit as emit_outbox
    with db_session() as s:
        node_sessions = (
            s.query(NodeSessionModel)
            .filter(NodeSessionModel.run_id == run_id)
            .all()
        )
        for ns in node_sessions:
            emit_outbox(s, NodeSpawned(
                node_session_id=ns.id,
                backend=ns.backend,
                backend_ref=ns.aionui_team_id or "",
                worktree=ns.worktree or "",
                ts=time.time(),
            ))
        s.commit()
    logger.info("Emitted %d NodeSpawned events for run %s", len(node_sessions), run_id)


def _handle_gate_evaluated(session, payload):
    """Finalize or advance a run based on the gate evaluation outcome.

    On ``pass`` -> merge the worktree via ``finalize_success``.
    On ``fail``  -> quarantine via ``finalize_failure``.
    Other outcomes -> advance to the next DAG node.
    """
    from backend.worktree.lifecycle import finalize_success, finalize_failure, _update_run
    from shared.db import session as db_session
    from shared.models import NodeSession as NodeSessionModel

    run_id = payload["run_id"]
    gate_outcome = payload.get("gate_outcome", "")

    # Auto-commit agent work before finalizing
    def _commit_worktree(run_id: str) -> None:
        """git add -A && git commit for the run's active worktree."""
        import subprocess
        with db_session() as s:
            ns = s.query(NodeSessionModel).filter(
                NodeSessionModel.run_id == run_id,
                NodeSessionModel.worktree.isnot(None),
            ).first()
            if not ns or not ns.worktree:
                logger.info("No worktree found for run %s — skipping commit", run_id)
                return
            wt = ns.worktree
            try:
                subprocess.run(["git", "add", "-A"], cwd=wt, capture_output=True, text=True, check=True, timeout=30)
                result = subprocess.run(["git", "status", "--porcelain"], cwd=wt, capture_output=True, text=True, timeout=30)
                if result.stdout.strip():
                    subprocess.run(["git", "commit", "-m", f"auto-commit run {run_id}"], cwd=wt, capture_output=True, text=True, check=True, timeout=30)
                    print(f"[PRINT] Auto-committed worktree changes for run {run_id}", flush=True)
                else:
                    logger.info("Nothing to commit for run %s", run_id)
            except subprocess.CalledProcessError as exc:
                logger.warning("Auto-commit failed for run %s: %s", run_id, exc.stderr)
                print(f"[PRINT] Auto-commit skipped for run {run_id}: {exc}", flush=True)

    if gate_outcome in ("pass", "done"):
        logger.info("Gate passed for run %s (%s) -- finalizing success", run_id, gate_outcome)
        print(f"[PRINT] Gate passed for run {run_id} ({gate_outcome})", flush=True)
        try:
            _commit_worktree(run_id)
            finalize_success(run_id, workspace_root=_WORKSPACE_ROOT)
            _update_run(run_id, state="done")
            print(f"[PRINT] Run {run_id} finalized and marked done", flush=True)
        except Exception as exc:
            logger.error("Failed to finalize run %s: %s", run_id, exc)
            print(f"[PRINT] FAILED to finalize run {run_id}: {exc}", flush=True)
    elif gate_outcome in ("fail", "failed"):
        logger.warning("Gate failed for run %s -- finalizing failure", run_id)
        print(f"[PRINT] Gate failed for run {run_id}", flush=True)
        try:
            finalize_failure(run_id, reason="gate_evaluated_fail", workspace_root=_WORKSPACE_ROOT)
            _update_run(run_id, state="failed")
            print(f"[PRINT] Run {run_id} quarantined and marked failed", flush=True)
        except Exception as exc:
            logger.error("Failed to quarantine run %s: %s", run_id, exc)
            print(f"[PRINT] FAILED to quarantine run {run_id}: {exc}", flush=True)
    else:
        logger.info(
            "Gate outcome %s for run %s -- dispatching next node",
            gate_outcome,
            run_id,
        )


def _handle_node_remediate(session, payload):
    """Attempt fix-forward by re-spawning the node with feedback.

    Loads the full plan from DB, finds the failed node, and creates
    a remediation session with the same worktree (fix-forward).
    """
    from backend.orchestration.spawn import spawn_node_team
    from backend.planning.store import get_plan

    from services.executor.aionui_client import AionUiClient
    from services.executor.worktree_manager import WorktreeManager

    run_id = payload["run_id"]
    node_id = payload["node_id"]
    plan_id = payload.get("plan_id", run_id)
    prev_session_id = payload.get("prev_session_id", "")
    attempt_next = payload.get("attempt_next", 1)

    logger.info(
        "Remediating node %s run %s attempt %d (prev session %s)",
        node_id, run_id, attempt_next, prev_session_id,
    )

    # Load the full plan so spawn_node_team has proper node data & members
    plan_data = get_plan(plan_id)
    if not plan_data:
        logger.error("Plan %s not found for remediation", plan_id)
        return

    # Find the specific node in the plan DAG
    dag = plan_data.get("dag", plan_data.get("nodes", []))
    node_data = next((n for n in dag if n.get("id") == node_id or n.get("node_id") == node_id), None)
    if not node_data:
        logger.error("Node %s not found in plan %s", node_id, plan_id)
        return

    aionui = AionUiClient(_AIONUI_HOST)
    wm = WorktreeManager(_WORKSPACE_ROOT)

    spawn_node_team(
        node=node_data,
        plan=plan_data,
        session_id=run_id,
        aionui=aionui,
        wm=wm,
    )


# ── Single dispatcher consumer ──────────────────────────────────────────────
# RabbitMQ round-robins messages across consumers on the same queue.
# Having 3 separate consumers means a gate.evaluated event can land on
# the plan-ratified handler and fail.  A single consumer dispatches by
# detecting the event type from payload fields instead.


def _executor_dispatcher(session, payload):
    """Dispatch incoming messages to the right handler by event type.

    ``start_consumer`` wrapper already handles dedup, ack/nack, and
    ``processed_events`` — this function only needs to route the payload.
    """
    if "gate_outcome" in payload:
        _handle_gate_evaluated(session, payload)
    elif "node_id" in payload and "attempt_next" in payload:
        _handle_node_remediate(session, payload)
    elif "plan_id" in payload:
        _handle_plan_ratified(session, payload)
    else:
        logger.warning("Unknown event type on executor.q: keys=%s", list(payload.keys()))


# ── Lifespan ────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: init DB, declare Rabbit topology, start consumer + relay."""
    init_db(cfg)
    bus.declare()
    bus.start_consumer("executor.q", _executor_dispatcher, "executor-dispatcher")
    relay_t = threading.Thread(target=bus.relay_loop, daemon=True)
    relay_t.start()
    consumer_t = threading.Thread(target=bus.start_consuming, daemon=True)
    consumer_t.start()
    logger.info("executor-svc ready")
    yield
    bus.close()


# ── FastAPI app ─────────────────────────────────────────────────────────────


app = FastAPI(title="executor-svc", version="0.1.0", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok", "service": "executor"}


# ── CLI entry point ─────────────────────────────────────────────────────────


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("EXECUTOR_PORT", "8091"))
    uvicorn.run(app, host="0.0.0.0", port=port)
