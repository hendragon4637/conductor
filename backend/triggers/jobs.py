from __future__ import annotations

import json
import os
import tempfile
import time
from typing import Any

import psycopg

import logging

from backend.aionui import AionUiClient, AionUiReader
from backend.observability.ingest import ingest_run
from backend.ratchet.detect import weak_configs, failing_traces
from backend.ratchet.mutate import propose_mutation
from backend.ratchet.experiment import run_experiment
from backend.ratchet.decide import decide
from backend.review import score_node, gather_evidence
from backend.evaluator.l3_calibrate import calibrate as run_calibrate
from contracts.version import CONTRACTS_VERSION

logger = logging.getLogger(__name__)

HOST = os.environ.get("AIONUI_HOST", "http://127.0.0.1:40937")
DB_PATH = os.environ.get(
    "AIONUI_DB",
    "/home/aipc/.config/AionUi/aionui/aionui-backend.db",
)


def run_task(payload: dict[str, Any]) -> dict[str, Any]:
    """Auto-spawn a plan from a stored intent, run, and score."""
    intent = payload.get("intent", "")
    if not intent:
        return {"status": "error", "message": "no intent in payload"}

    client = AionUiClient(HOST)
    workspace = payload.get("workspace", "")
    if not workspace:
        workspace = tempfile.mkdtemp(prefix="cron-")

    conv_id = client.create_conversation(
        preset_agent_type="acp",
        workspace=workspace,
        model=payload.get("model", "nvidia/openai/gpt-oss-120b"),
    )
    client.send_message(conv_id, intent)

    import time
    time.sleep(15)

    reader = AionUiReader(DB_PATH)
    msgs = reader.messages_for(conv_id)
    reader.close()

    trace_id = ingest_run(
        task_id=payload.get("task_id", f"cron-{conv_id[:8]}"),
        plan_id=payload.get("plan_id", "cron-auto"),
        agent_config=payload.get("agent_config", "opencode:backend-executor"),
        engine="opencode",
        model=payload.get("model", "nvidia/openai/gpt-oss-120b"),
        conversation_id=conv_id,
        db_path=DB_PATH,
    )

    evidence = gather_evidence(
        worktree_path=workspace,
        conversation_messages=msgs,
    )
    node = {
        "id": "cron-task",
        "agent_config": payload.get("agent_config", "opencode:backend-executor"),
        "role": "executor",
        "success": payload.get("success_criterion", ""),
    }
    result = score_node(node, trace_id, evidence)

    return {
        "status": "ok",
        "conv_id": conv_id,
        "trace_id": trace_id,
        "score": result,
    }


def enrich(payload: dict[str, Any]) -> dict[str, Any]:
    """Run varied scenario intents to grow score history."""
    intents = payload.get("intents", [])
    if not intents:
        intents = ["list files in the current directory"]

    results = []
    for intent in intents:
        result = run_task({
            "intent": intent,
            "agent_config": payload.get("agent_config", "opencode:backend-executor"),
            "model": payload.get("model", "nvidia/openai/gpt-oss-120b"),
            "success_criterion": payload.get("success_criterion", ""),
        })
        results.append(result)

    return {
        "status": "ok",
        "enriched_count": len(results),
        "results": results,
    }


def _emit_ratchet_trigger(
    agent_config_id: str,
    node_type: str = "executor",
) -> None:
    """Write a ``RatchetTrigger`` event to the transactional outbox.

    The outbox relay in evaluator-svc picks this up, publishes it to
    RabbitMQ on the ``ratchet.trigger`` routing key, and evaluator-svc's
    ``on_ratchet_trigger`` handler runs ``backend.evaluator.ratchet.run_experiment()``.

    This bridges the monolith ``ratchet_sweep`` job into the microservice
    event-driven ratchet pipeline.
    """
    db = os.environ.get("DATABASE_URL", "")
    if not db:
        return
    payload = json.dumps({
        "agent_config_id": agent_config_id,
        "node_type": node_type,
        "env": "staging",
        "ts": time.time(),
    })
    with psycopg.connect(db) as c:
        with c.cursor() as cur:
            cur.execute(
                "INSERT INTO outbox (routing_key, payload, contracts_version) "
                "VALUES (%s, %s, %s)",
                ("ratchet.trigger", payload, CONTRACTS_VERSION),
            )
        c.commit()


def ratchet_sweep(payload: dict[str, Any]) -> dict[str, Any]:
    """Detect weak configs, propose mutation, emit trigger or run experiment.

    When ``propose_only=True`` (the default), the function emits a
    ``RatchetTrigger`` to the outbox for each weak config instead of
    running the experiment inline.  The evaluator-svc consumes these
    events and runs ``backend.evaluator.ratchet.run_experiment()``.

    When ``propose_only=False``, the legacy inline path runs the full
    experiment+decide cycle directly.
    """  # noqa: E501
    threshold = payload.get("threshold", 0.7)
    min_runs = payload.get("min_runs", 5)
    propose_only = payload.get("propose_only", True)

    weak = weak_configs(threshold=threshold, min_runs=min_runs)
    if not weak:
        return {"status": "ok", "message": "no weak configs found"}

    results = []
    for agent_cfg in weak:
        traces = failing_traces(agent_config=agent_cfg, limit=5)
        if not traces:
            continue

        mutation = propose_mutation(agent_cfg, traces)
        if not mutation.get("target"):
            continue

        if propose_only:
            # Emit RatchetTrigger — evaluator-svc runs the experiment asynchronously
            _emit_ratchet_trigger(agent_cfg)
            results.append({
                "agent_config": agent_cfg,
                "mutation": mutation,
                "applied": False,
                "reason": "propose_only mode — RatchetTrigger emitted for async experiment",
            })
        else:
            exp = run_experiment(
                agent_config=agent_cfg,
                mutation=mutation,
                dataset=agent_cfg,
                max_tasks=payload.get("max_tasks", 0),
            )
            decision = decide(
                exp["experiment_id"],
                delta_threshold=payload.get("delta_threshold", 0.03),
            )
            results.append({
                "agent_config": agent_cfg,
                "mutation": mutation,
                "experiment_id": exp["experiment_id"],
                "decision": decision,
                "baseline_score": exp["baseline_score"],
                "candidate_score": exp["candidate_score"],
                "applied": decision == "kept",
            })

    return {"status": "ok", "sweep_count": len(results), "results": results}


def calibrate_sweep(payload: dict[str, Any]) -> dict[str, Any]:
    """Run L3 calibration for all active node types with golden data.

    Loads distinct node_types from the ``golden_set`` table and runs
    ``calibrate()`` for each.  Results are persisted to ``judge_trust``
    and logged to Langfuse inside ``calibrate()``.
    """
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        return {"status": "error", "message": "no DATABASE_URL"}

    node_types: list[str] = []
    try:
        import psycopg

        with psycopg.connect(url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT DISTINCT node_type FROM golden_set WHERE frozen = TRUE ORDER BY node_type"
                )
                node_types = [row[0] for row in cur.fetchall()]
    except Exception as exc:
        logger.exception("Failed to load node_types from golden_set: %s", exc)
        return {"status": "error", "message": str(exc)[:200]}

    if not node_types:
        return {"status": "ok", "message": "no frozen golden items found", "calibrated": []}

    results = []
    for node_type in node_types:
        try:
            report = run_calibrate(node_type)
            results.append({
                "node_type": node_type,
                "trusted": report.trusted,
                "agreement": report.agreement,
                "mae": report.mae,
                "total": report.total,
                "note": report.note,
            })
            logger.info(
                "Calibrated %s: agreement=%.4f mae=%.4f trusted=%s total=%d",
                node_type, report.agreement, report.mae, report.trusted, report.total,
            )
        except Exception as exc:
            logger.exception("Calibration failed for node_type=%s: %s", node_type, exc)
            results.append({
                "node_type": node_type,
                "error": str(exc)[:200],
            })

    return {"status": "ok", "calibrated": results}
