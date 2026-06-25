from __future__ import annotations

import os
import tempfile
from typing import Any

from backend.aionui import AionUiClient, AionUiReader
from backend.observability.ingest import ingest_run
from backend.ratchet.detect import weak_configs, failing_traces
from backend.ratchet.mutate import propose_mutation
from backend.ratchet.experiment import run_experiment
from backend.ratchet.decide import decide
from backend.review import score_node, gather_evidence

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


def ratchet_sweep(payload: dict[str, Any]) -> dict[str, Any]:
    """Detect weak configs, propose mutation, experiment, decide (propose-only by default)."""  # noqa: E501
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
            results.append({
                "agent_config": agent_cfg,
                "mutation": mutation,
                "applied": False,
                "reason": "propose_only mode — human approval required for global scope",
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
