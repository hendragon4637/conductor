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


def _fix_worktree_perms(wt: str) -> None:
    """Fix root-owned files in the worktree (Hermes Docker sandbox writes as root).

    Runs an ephemeral alpine container to chown the worktree to the host ``aipc``
    user (UID 1001).  Silently no-ops if Docker is unavailable.
    """
    import subprocess
    try:
        subprocess.run(
            ["docker", "run", "--rm", "-v", f"{wt}:/wt", "alpine:latest",
             "chown", "-R", "1001:1001", "/wt"],
            capture_output=True, text=True, check=True, timeout=60,
        )
        logger.info("Fixed worktree permissions for %s", wt)
    except Exception as exc:
        logger.warning("Could not fix worktree permissions for %s: %s", wt, exc)


def _commit_worktree(run_id: str, node_id: str | None = None) -> None:
    """git add -A && git commit for the run's active worktree.

    When *node_id* is provided, creates a ``node-{node_id}`` tag pointing
    to the commit so downstream nodes can reference it via
    ``build_node_context`` (which calls ``git show node-{node_id}``).
    """
    from shared.db import session as db_session
    from shared.models import NodeSession as NodeSessionModel
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
                _tag_worktree_commit(wt, node_id)
                print(f"[PRINT] Auto-committed worktree changes for run {run_id}", flush=True)
            else:
                logger.info("Nothing to commit for run %s", run_id)
        except subprocess.CalledProcessError as exc:
            if "Permission denied" in exc.stderr:
                logger.warning("Permission denied during commit — Hermes root-owned files; fixing perms and retrying")
                _fix_worktree_perms(wt)
                try:
                    subprocess.run(["git", "add", "-A"], cwd=wt, capture_output=True, text=True, check=True, timeout=30)
                    subprocess.run(["git", "commit", "-m", f"auto-commit run {run_id}"], cwd=wt, capture_output=True, text=True, check=True, timeout=30)
                    _tag_worktree_commit(wt, node_id)
                    print(f"[PRINT] Auto-committed worktree changes for run {run_id} (after permission fix)", flush=True)
                    return
                except subprocess.CalledProcessError as retry_exc:
                    logger.warning("Auto-commit still failed after permission fix: %s", retry_exc.stderr)
            else:
                logger.warning("Auto-commit failed for run %s: %s", run_id, exc.stderr)
            print(f"[PRINT] Auto-commit skipped for run {run_id}: {exc}", flush=True)


def _tag_worktree_commit(wt: str, node_id: str | None) -> None:
    """Create a ``node-{node_id}`` tag in the worktree git repo."""
    if not node_id:
        return
    import subprocess
    tag = f"node-{node_id}"
    try:
        subprocess.run(["git", "tag", "-d", tag], cwd=wt, capture_output=True, text=True, timeout=15)
        subprocess.run(["git", "tag", tag], cwd=wt, capture_output=True, text=True, check=True, timeout=15)
        print(f"[PRINT] Tagged commit as {tag}", flush=True)
    except subprocess.CalledProcessError as exc:
        logger.warning("Failed to tag %s: %s", tag, exc.stderr)


def _persist_commit_tag(run_id: str, node_id: str | None, node_session_id: str) -> None:
    """Write the git tag name back to node_sessions.commit_tag so the tag
    the executor just created (e.g. ``node-004``) is traceable via the DB."""
    if not node_id or not node_session_id:
        return
    from shared.models import NodeSession as _NSModel
    from shared.db import session as _db_session
    tag = f"node-{node_id}"
    try:
        with _db_session() as s:
            s.query(_NSModel).filter(_NSModel.id == node_session_id).update(
                {"commit_tag": tag}
            )
            s.commit()
        logger.info("Persisted commit_tag=%s for node_session %s", tag, node_session_id)
    except Exception as exc:
        logger.warning("Failed to persist commit_tag=%s: %s", tag, exc)


def _finalize_or_advance(run_id: str, payload: dict[str, object]) -> None:
    """Check DAG for more ready nodes; spawn next or finalize as success.

    Queries the plan DAG and all node_sessions for the run.  If a pending
    node has all its dependencies satisfied, spawns it.  If all nodes are
    complete, finalizes the run as success.
    """
    from backend.planning.store import get_plan
    from backend.orchestration.spawn import spawn_node_team
    from backend.worktree.lifecycle import finalize_success, _update_run
    from services.executor.aionui_client import AionUiClient
    from services.executor.worktree_manager import WorktreeManager
    from shared.db import session as db_session
    from shared.models import NodeSession as NodeSessionModel, Run as RunModel
    from contracts.events import NodeSpawned, RunCompleted
    from shared.outbox import emit
    import time
    from typing import Any

    # 1. Get plan_id from runs table
    with db_session() as s:
        run_row = s.query(RunModel).filter(RunModel.id == run_id).first()
    if not run_row:
        logger.error("Run %s not found — cannot advance DAG", run_id)
        return
    plan_id = str(run_row.plan_id)

    # 2. Load plan (has DAG)
    plan_data = get_plan(plan_id)
    if not plan_data:
        logger.error("Plan %s not found — cannot advance DAG", plan_id)
        return
    dag = plan_data.get("dag", plan_data.get("nodes", []))
    if not dag:
        logger.error("Plan %s has no DAG — cannot advance", plan_id)
        return

    # 3. Load all node_sessions for this run
    with db_session() as s:
        node_sessions = (
            s.query(NodeSessionModel)
            .filter(NodeSessionModel.run_id == run_id)
            .all()
        )

    # Build completed / pending sets
    completed_ids: set[str] = set()
    session_map: dict[str, NodeSessionModel] = {}
    for ns in node_sessions:
        session_map[str(ns.node_id)] = ns
        if ns.verdict in ("done_no_change", "done_with_change", "failed", "crashed"):
            completed_ids.add(str(ns.node_id))
        elif ns.gate_outcome in ("pass", "done"):
            completed_ids.add(str(ns.node_id))

    # 4. Find next ready node (pending + all deps satisfied)
    next_node: dict[str, Any] | None = None
    for n in dag:
        if not isinstance(n, dict):
            continue
        nid = str(n.get("id") or n.get("node_id") or "")
        if not nid or nid in completed_ids:
            continue
        ns = session_map.get(nid)
        if ns and ns.verdict == "running":
            continue  # already running
        deps = n.get("depends_on", [])
        if isinstance(deps, list) and all(d in completed_ids for d in deps):
            next_node = n
            break

    if next_node:
        next_nid = str(next_node.get("id") or next_node.get("node_id") or "")
        logger.info("Advancing DAG: spawning node %s for run %s", next_nid, run_id)
        print(f"[PRINT] Advancing DAG: spawning node {next_nid} for run {run_id}", flush=True)

        aionui = AionUiClient(_AIONUI_HOST)
        wm = WorktreeManager(_WORKSPACE_ROOT)

        # Find the pending node_session for this node
        ns_id: str | None = None
        existing_ns = session_map.get(next_nid)
        if existing_ns is not None:
            ns_id = str(existing_ns.id)
            with db_session() as s:
                s.query(NodeSessionModel).filter(NodeSessionModel.id == ns_id).update({
                    NodeSessionModel.verdict: "running",
                })
                s.commit()

        if not ns_id:
            logger.error("No node_session found for node %s (run %s)", next_nid, run_id)
            return

        worktree_path = str(plan_data.get("worktree_path") or (existing_ns.worktree if existing_ns else "") or "")
        plan_data["worktree_path"] = worktree_path  # prevent spawn_node_team from creating a duplicate worktree
        session_id = run_id  # microservice convention
        members_raw = next_node.get("members", [next_node.get("agent_config", "opencode:backend-executor")])
        if not isinstance(members_raw, list):
            members_raw = [str(members_raw)]

        from backend.builtins.handoff import build_node_context as _build_dep_context
        dep_ids = next_node.get("depends_on", [])
        dep_context = _build_dep_context(worktree_path, dep_ids) if worktree_path and dep_ids else ""

        try:
            conv_map = spawn_node_team(
                node=next_node,
                plan=plan_data,
                session_id=session_id,
                aionui=aionui,
                wm=wm,
                members=members_raw,
                dep_context=dep_context,
                workspace_root=_WORKSPACE_ROOT,
                auto_approve=plan_data.get("auto_approve", True),
            )
        except Exception as exc:
            logger.exception("Failed to spawn node %s for run %s", next_nid, run_id)
            print(f"[PRINT] FAILED to spawn node {next_nid}: {exc}", flush=True)
            return

        # Extract IDs from spawn result
        orch_conv = conv_map.get("orchestrator")
        if not orch_conv:
            for k, v in conv_map.items():
                if not k.startswith("__"):
                    orch_conv = v
                    break
        team_id = conv_map.get("__team_id__")
        if not team_id and conv_map.get("__run_id__"):
            team_id = conv_map["__run_id__"]

        # Update node_session with spawn results
        with db_session() as s:
            s.query(NodeSessionModel).filter(NodeSessionModel.id == ns_id).update({
                NodeSessionModel.aionui_conversation_id: orch_conv or "",
                NodeSessionModel.aionui_team_id: team_id or "",
            })
            s.commit()

        # Emit NodeSpawned so watcher-svc picks up this session
        with db_session() as s:
            emit(s, NodeSpawned(
                node_session_id=ns_id,
                backend=str(next_node.get("backend", "opencode")),
                backend_ref=team_id or "",
                worktree=worktree_path or "",
                ts=time.time(),
            ))
            s.commit()

        logger.info("Advanced DAG: spawned node %s (ns=%s) for run %s", next_nid, ns_id, run_id)
        print(f"[PRINT] Advanced DAG: spawned node {next_nid} (ns={ns_id})", flush=True)

    else:
        # No more ready nodes — check if ALL nodes are done
        total = len(dag)
        completed = len(completed_ids)
        logger.info("DAG check: %d/%d nodes completed for run %s", completed, total, run_id)
        print(f"[PRINT] DAG check: {completed}/{total} nodes completed for run {run_id}", flush=True)

        if completed >= total:
            logger.info("All %d nodes complete for run %s — finalizing success", total, run_id)
            print(f"[PRINT] All {total} nodes complete for run {run_id} — finalizing success", flush=True)
            try:
                from contracts.events import RunMerged
                merged_run = finalize_success(run_id, workspace_root=_WORKSPACE_ROOT)
                _update_run(run_id, state="done")

                if (merged_run or {}).get("merge_status") == "blocked":
                    # Outcome stays success — quality passed, integration failed.
                    # Emit RunCompleted so the L4 chain still runs (L4 never
                    # depends on merge succeeding); NO RunMerged — master did
                    # not advance, so pending goals must not drain.
                    with db_session() as s:
                        emit(s, RunCompleted(
                            run_id=run_id,
                            plan_id=plan_id,
                            status="done",
                            worktree_status="blocked",
                            ts=time.time(),
                        ))
                        s.commit()
                    print(f"[PRINT] Run {run_id} done but merge blocked — project paused", flush=True)
                else:
                    # Record dependency SHAs before emitting RunMerged
                    try:
                        from services.planner.system_goal import record_dep_shas
                        record_dep_shas(run_row.project_id or plan_data.get("project_id", ""), run_id)
                    except Exception as exc:
                        logger.warning("Failed to record dep_shas for run %s: %s", run_id, exc)

                    with db_session() as s:
                        # Re-read run to get updated dep_shas
                        updated_run = s.query(RunModel).filter(RunModel.id == run_id).first()
                        dep_shas = updated_run.dep_shas if updated_run else None

                        emit(s, RunCompleted(
                            run_id=run_id,
                            plan_id=plan_id,
                            status="done",
                            worktree_status="merged",
                            ts=time.time(),
                        ))
                        emit(s, RunMerged(
                            run_id=run_id,
                            plan_id=plan_id,
                            project_id=plan_data.get("project_id", "") or (run_row.project_id if run_row else ""),
                            merge_commit=merged_run.get("merge_commit") if merged_run else None,
                            dep_shas=dep_shas,
                            ts=time.time(),
                        ))
                        s.commit()

                    # Post-merge image pipeline (opt-in; never affects outcome)
                    try:
                        from backend.worktree.lifecycle import finalize_image
                        finalize_image(run_id, workspace_root=_WORKSPACE_ROOT)
                    except Exception as exc:
                        logger.warning("finalize_image failed for run %s: %s", run_id, exc)

                    # File 10: publish on run.merged — worksystem is derived
                    # state, so a failed publish never affects the run outcome.
                    try:
                        from backend.worksystem.publish import publish_run
                        publish_run(run_id, workspace_root=_WORKSPACE_ROOT)
                    except Exception as exc:
                        logger.warning("publish failed for run %s: %s", run_id, exc)

                    print(f"[PRINT] Run {run_id} finalized and marked done", flush=True)
            except Exception as exc:
                logger.error("Failed to finalize run %s: %s", run_id, exc)
                print(f"[PRINT] FAILED to finalize run {run_id}: {exc}", flush=True)
        else:
            logger.info("DAG incomplete (%d/%d) — waiting for more nodes for run %s", completed, total, run_id)
            print(f"[PRINT] DAG incomplete ({completed}/{total}) — waiting for more nodes", flush=True)


def _handle_gate_evaluated(session, payload):
    """Finalize or advance a run based on the gate evaluation outcome.

    On ``done``/``pass`` -> commit worktree, check DAG for next node to
    spawn, or finalize success if all nodes complete.
    On ``fail``/``failed`` -> quarantine via ``finalize_failure``.
    On ``remediate`` -> log (handled by NodeRemediate event).
    On other outcomes -> log and let the DAG advancement decide.
    """
    from backend.worktree.lifecycle import finalize_success, finalize_failure, _update_run
    from shared.db import session as _db_session
    from shared.models import NodeSession as _NodeSessionModel

    run_id = payload["run_id"]
    gate_outcome = payload.get("gate_outcome", "")
    node_session_id = payload.get("node_session_id", "")

    _node_id: str | None = None
    if node_session_id:
        with _db_session() as s:
            _ns_row = s.query(_NodeSessionModel).filter(_NodeSessionModel.id == node_session_id).first()
            if _ns_row:
                _node_id = str(_ns_row.node_id)

    if gate_outcome in ("pass", "done"):
        logger.info("Gate passed for run %s (%s) -- committing + checking DAG", run_id, gate_outcome)
        print(f"[PRINT] Gate passed for run {run_id} ({gate_outcome})", flush=True)
        _commit_worktree(run_id, node_id=_node_id)
        # Persist commit_tag to node_sessions so git tag is traceable via DB
        _persist_commit_tag(run_id, _node_id, node_session_id)
        _finalize_or_advance(run_id, payload)
    elif gate_outcome in ("fail", "failed"):
        logger.warning("Gate failed for run %s -- finalizing failure", run_id)
        print(f"[PRINT] Gate failed for run {run_id}", flush=True)
        try:
            from contracts.events import RunFailed
            from shared.outbox import emit

            _commit_worktree(run_id, node_id=_node_id)
            # Persist commit_tag even on failure for forensic traceability
            _persist_commit_tag(run_id, _node_id, node_session_id)
            finalize_failure(run_id, reason="gate_evaluated_fail", workspace_root=_WORKSPACE_ROOT)
            _update_run(run_id, state="failed")
            emit(session, RunFailed(
                run_id=run_id,
                reason="gate_evaluated_fail",
                quarantine_tag=node_session_id or _node_id,
                ts=time.time(),
            ))
            print(f"[PRINT] Run {run_id} quarantined, marked failed, RunFailed emitted", flush=True)
        except Exception as exc:
            logger.error("Failed to quarantine run %s: %s", run_id, exc)
            print(f"[PRINT] FAILED to quarantine run {run_id}: {exc}", flush=True)
    elif gate_outcome == "remediate":
        logger.info(
            "Gate outcome: remediate — remediation dispatched for run %s",
            run_id,
        )
    else:
        logger.info(
            "Gate outcome %s for run %s -- dispatching next node",
            gate_outcome,
            run_id,
        )
        _finalize_or_advance(run_id, payload)


def _handle_node_steer(session, payload):
    """Reuse the existing AionUi conversation and send a steering brief.

    Instead of spawning a brand new team (remediation), this handler:
    1. Loads the previous node_session and reuses its aionui_conversation_id.
    2. Builds a steering brief from evaluator feedback.
    3. Sends the brief as a message to the existing conversation.
    4. Creates a new node_session with steering_count+1 (same conv_id).
    5. Emits NodeSpawned so watcher-svc resumes polling the conversation.
    """
    from backend.evaluator.steering import build_steering_brief
    from backend.planning.store import get_plan
    from services.executor.aionui_client import AionUiClient
    from shared.db import session as db_session
    from shared.models import NodeSession as NodeSessionModel
    from sqlalchemy.sql import func
    from contracts.events import NodeSpawned
    from shared.outbox import emit
    import uuid

    run_id = payload["run_id"]
    node_id = payload["node_id"]
    prev_session_id = payload.get("session_id", "")
    feedback_ref = payload.get("feedback_ref", prev_session_id)
    worktree = payload.get("worktree", "")
    steering_count = payload.get("steering_count", 0)

    logger.info(
        "Steering node %s run %s (steering_count=%d, prev_session=%s)",
        node_id, run_id, steering_count, prev_session_id,
    )

    # 1. Load previous session to get conversation_id
    with db_session() as s:
        prev_ns = s.query(NodeSessionModel).filter(NodeSessionModel.id == prev_session_id).first()
        if prev_ns is None:
            logger.error("Previous session %s not found — cannot steer", prev_session_id)
            return
        conv_id = prev_ns.aionui_conversation_id
        if not conv_id:
            logger.error("Previous session %s has no aionui_conversation_id — cannot steer", prev_session_id)
            return

        # Extract values needed outside the session before it closes
        prev_attempt = prev_ns.attempt
        prev_team_id = prev_ns.aionui_team_id

        # Close previous session with gate_outcome='steer'
        s.query(NodeSessionModel).filter(NodeSessionModel.id == prev_session_id).update({
            NodeSessionModel.gate_outcome: "steer",
            NodeSessionModel.finished_at: func.now(),
        })
        s.commit()

    # 2. Load plan and find the node definition
    plan_id = payload.get("plan_id", "")
    if not plan_id:
        from shared.models import Run as RunModel
        with db_session() as s:
            run_row = s.query(RunModel).filter(RunModel.id == run_id).first()
            plan_id = run_row.plan_id if run_row else run_id
    plan_data = get_plan(plan_id)
    if not plan_data:
        logger.error("Plan %s not found for steering", plan_id)
        return
    dag = plan_data.get("dag", plan_data.get("nodes", []))
    node_data = next((n for n in dag if n.get("id") == node_id), None)
    if not node_data:
        logger.error("Node %s not found in plan %s", node_id, plan_id)
        return

    # 3. Load feedback and build steering brief
    feedback = {}
    with db_session() as s:
        fb_ns = s.query(NodeSessionModel).filter(NodeSessionModel.id == feedback_ref).first()
        if fb_ns is not None and fb_ns.feedback is not None:
            raw = fb_ns.feedback
            if isinstance(raw, dict):
                feedback = raw
    original_task = (node_data.get("task") or {}).get("text", "")
    success_criterion = (node_data.get("success") or {}).get("text", "")
    brief = build_steering_brief(original_task, success_criterion, feedback, steering_count)
    logger.info("Built steering brief for node %s (steering attempt %d)", node_id, steering_count + 1)

    # 4. Create new node_session with steering_count+1 and same conversation_id
    new_ns_id = f"ns_{uuid.uuid4().hex[:8]}"
    backend = node_data.get("backend", "opencode")
    with db_session() as s:
        new_ns = NodeSessionModel(
            id=new_ns_id,
            run_id=run_id,
            node_id=node_id,
            backend=backend,
            verdict="running",
            worktree=worktree,
            attempt=prev_attempt,
            steering_count=steering_count + 1,
            remediation_of=prev_session_id,
            aionui_conversation_id=conv_id,
            aionui_team_id=prev_team_id,
            feedback=feedback,
        )
        s.add(new_ns)
        s.commit()
        logger.info(
            "Created steering session %s (steering_count=%d) reusing conv=%s",
            new_ns_id, steering_count + 1, conv_id,
        )

    # 5. Cancel any running turn so AionUi doesn't reject with 409
    aionui = AionUiClient(_AIONUI_HOST)
    aionui.cancel_conversation(conv_id)

    # 6. Send steering brief to the existing conversation
    team_id = prev_team_id
    try:
        if team_id:
            team_info = aionui.get_team(team_id)
            slot_id = ""
            for agent in team_info.get("agents", []):
                if agent.get("conversation_id") == conv_id:
                    slot_id = agent.get("slot_id", "")
                    break
            if slot_id:
                aionui.send_team_message(team_id, slot_id, brief)
            else:
                logger.warning(
                    "No slot_id for conv %s in team %s — fallback to direct message",
                    conv_id, team_id,
                )
                aionui.send_message(conv_id, brief)
        else:
            aionui.send_message(conv_id, brief)
        logger.info("Sent steering brief to conv=%s for session %s", conv_id, new_ns_id)
    except Exception:
        logger.exception("Failed to send steering brief to conv=%s", conv_id)
        return

    # 6. Emit NodeSpawned so watcher-svc picks up the new session
    with db_session() as s:
        emit(s, NodeSpawned(
            node_session_id=new_ns_id,
            backend=backend,
            backend_ref=conv_id,
            worktree=worktree,
            ts=time.time(),
        ))
        s.commit()

    logger.info(
        "Steering complete for node %s run %s — session %s reusing conv=%s",
        node_id, run_id, new_ns_id, conv_id,
    )


def _handle_node_remediate(session, payload):
    """Attempt fix-forward by re-spawning the node with feedback.

    Creates a new node_session with correct attempt tracking,
    remediation_of linking, remediation brief, and feedback passing.
    Closes the previous session row with gate_outcome='remediate'.
    """
    from backend.orchestration.spawn import spawn_node_team
    from backend.planning.store import get_plan
    from backend.evaluator.remediation import build_remediation_brief

    from services.executor.aionui_client import AionUiClient
    from services.executor.worktree_manager import WorktreeManager
    from shared.db import session as db_session
    from shared.models import NodeSession as NodeSessionModel
    from sqlalchemy.sql import func
    import uuid

    run_id = payload["run_id"]
    node_id = payload["node_id"]
    plan_id = payload.get("plan_id")
    if not plan_id:
        from shared.models import Run as RunModel
        from shared.db import session as db_session
        with db_session() as s:
            run_row = s.query(RunModel).filter(RunModel.id == run_id).first()
            plan_id = run_row.plan_id if run_row else run_id
    prev_session_id = payload.get("prev_session_id", "")
    attempt_next = payload.get("attempt_next", 1)
    feedback_ref = payload.get("feedback_ref", prev_session_id)
    worktree = payload.get("worktree", "")

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

    # 1. Close previous session with gate_outcome='remediate'
    with db_session() as s:
        updated = (
            s.query(NodeSessionModel)
            .filter(NodeSessionModel.id == prev_session_id)
            .update({
                NodeSessionModel.gate_outcome: "remediate",
                NodeSessionModel.finished_at: func.now(),
            })
        )
        s.commit()
        if updated:
            logger.info("Closed previous session %s with gate_outcome=remediate", prev_session_id)
        else:
            logger.warning("Previous session %s not found — proceeding without close", prev_session_id)

    # 2. Load feedback from feedback_ref node_session
    feedback = {}
    with db_session() as s:
        fb_ns = s.query(NodeSessionModel).filter(NodeSessionModel.id == feedback_ref).first()
        if fb_ns is not None and fb_ns.feedback is not None:
            raw = fb_ns.feedback
            if isinstance(raw, dict):
                feedback = raw
            logger.info(
                "Loaded feedback from session %s (%d failed checks)",
                feedback_ref, len(feedback.get("failed_checks", [])),
            )
        else:
            logger.warning("No feedback found for feedback_ref %s", feedback_ref)

    # 3. Build remediation brief (original goal + failed checks + fix-forward instruction)
    original_task = (node_data.get("task") or {}).get("text", "")
    success_criterion = (node_data.get("success") or {}).get("text", "")
    brief = build_remediation_brief(original_task, success_criterion, feedback)
    logger.info("Built remediation brief for node %s (attempt %d)", node_id, attempt_next)

    # 4. Create new node_session for this remediation attempt
    new_ns_id = f"ns_{uuid.uuid4().hex[:8]}"
    backend = node_data.get("backend", "opencode")

    with db_session() as s:
        new_ns = NodeSessionModel(
            id=new_ns_id,
            run_id=run_id,
            node_id=node_id,
            backend=backend,
            verdict="running",
            worktree=worktree,
            attempt=attempt_next,
            remediation_of=prev_session_id,
            feedback=feedback,
        )
        s.add(new_ns)
        s.commit()
        logger.info(
            "Created remediation session %s for node %s attempt %d remediation_of=%s",
            new_ns_id, node_id, attempt_next, prev_session_id,
        )

    # 5. Override node task with remediation brief (same members, same config)
    node_with_brief = dict(node_data)
    node_with_brief["task"] = {"text": brief}

    # Extract members from the original plan node so spawn_node_team resolves
    # the correct agent_config (e.g. python-development-fastapi-pro) instead
    # of falling back to "opencode:backend-executor".  The node dict has no
    # top-level agent_config key — it lives inside members[].agent_config.
    members_raw = node_data.get("members", [node_data.get("agent_config", "opencode:backend-executor")])
    if not isinstance(members_raw, list):
        members_raw = [str(members_raw)]

    aionui = AionUiClient(_AIONUI_HOST)
    wm = WorktreeManager(_WORKSPACE_ROOT)

    # Ensure spawn reuses existing worktree (fix-forward, do NOT create new)
    plan_data["worktree_path"] = worktree

    from backend.builtins.handoff import build_node_context as _build_dep_context
    dep_ids = node_data.get("depends_on", [])
    dep_context = _build_dep_context(worktree, dep_ids) if worktree and dep_ids else ""

    # 6. Spawn fix-forward remediation team (same members+config as original node)
    try:
        conv_map = spawn_node_team(
            node=node_with_brief,
            plan=plan_data,
            session_id=run_id,
            aionui=aionui,
            wm=wm,
            members=members_raw,
            dep_context=dep_context,
            workspace_root=_WORKSPACE_ROOT,
            auto_approve=plan_data.get("auto_approve", True),
        )
    except Exception:
        logger.exception("Failed to spawn remediation for node %s run %s", node_id, run_id)
        return

    # 7. Update new session with aionui IDs from spawn
    orch_conv = conv_map.get("orchestrator")
    if not orch_conv:
        for k, v in conv_map.items():
            if not k.startswith("__"):
                orch_conv = v
                break
    team_id = conv_map.get("__team_id__")

    if orch_conv and new_ns_id:
        with db_session() as s:
            s.query(NodeSessionModel).filter(NodeSessionModel.id == new_ns_id).update({
                NodeSessionModel.aionui_conversation_id: orch_conv,
                NodeSessionModel.aionui_team_id: team_id,
            })
            s.commit()
            logger.info(
                "Updated session %s with aionui conv=%s team=%s",
                new_ns_id, orch_conv, team_id,
            )
    else:
        logger.warning("No conversation_id from spawn — session %s not updated with aionui IDs", new_ns_id)

    logger.info(
        "Remediation complete for node %s run %s attempt %d — session %s",
        node_id, run_id, attempt_next, new_ns_id,
    )


# ── Single dispatcher consumer ──────────────────────────────────────────────
# ── Run stop handler ──────────────────────────────────────────────────────


def _handle_run_stop(session, payload):
    """Handle RunStop — cancel the run, emit RunStopped back to planner."""
    from backend.worktree.lifecycle import _update_run
    from contracts.events import RunStopped
    from shared.outbox import emit
    import time

    run_id = payload["run_id"]
    project_id = payload.get("project_id", "")
    reason = payload.get("reason", "no reason")

    logger.info("RunStop received for run=%s project=%s reason=%s", run_id, project_id, reason)

    _update_run(run_id, state="cancelled")

    emit(session, RunStopped(
        run_id=run_id,
        project_id=project_id,
        reason=reason,
    ))
    logger.info("Run %s cancelled and RunStopped emitted", run_id)


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
    elif "steering_count" in payload and "session_id" in payload:
        _handle_node_steer(session, payload)
    elif "node_id" in payload and "attempt_next" in payload:
        _handle_node_remediate(session, payload)
    elif "plan_id" in payload:
        _handle_plan_ratified(session, payload)
    elif "run_id" in payload and "reason" in payload:
        _handle_run_stop(session, payload)
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
