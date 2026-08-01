"""FastAPI entrypoint for evaluator-svc.

Initialises the database, declares RabbitMQ topology, and starts
event consumers for ``node.observed`` and ``ratchet.trigger``.

Usage:
    python -m services.evaluator.main
"""

from __future__ import annotations

import logging
import os
import re
import json
import shutil
import stat
import subprocess
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pika
import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel

from contracts.events import GateEvaluated, NodeSteer, NodeRemediate, CalibrateTrigger, L4Findings
from shared.bus import EventBus, RequeueHandled
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

# ── Configurable limits ───────────────────────────────────────────────────

MAX_STEERING_ATTEMPTS = int(os.environ.get("MAX_STEERING_ATTEMPTS", "10"))
"""Max steering attempts before switching to full remediation."""

STAGNATION_LIMIT = int(os.environ.get("STAGNATION_LIMIT", "3"))
"""Consecutive ``done_no_change`` verdicts with no L2 improvement before forced failure."""

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
        steering_count = ns.steering_count or 0
        if steering_count < MAX_STEERING_ATTEMPTS:
            emit(s, NodeSteer(
                run_id=ns.run_id,
                node_id=ns.node_id,
                session_id=ns.id,
                feedback_ref=ns.id,
                worktree=ns.worktree or "",
                steering_count=steering_count,
                ts=time.time(),
            ))
        else:
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

    # Role filter: planning sessions are handled by planner-svc, not evaluator
    role = getattr(ns, "role", "execution")
    if role == "planning":
        logger.info("NodeSession %s is role=planning — evaluator skips", node_session_id)
        return
    # L4 sessions have their own handler (watcher-observed)
    if role == "l4":
        from services.evaluator.l4_runner import _on_l4_observed
        _on_l4_observed(s, payload)
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

    from contracts.plan_assembler import Check as CheckModel
    raw_checks: list[dict] = node_def.get("checks", [])
    check_list: list[Any] = [
        CheckModel(**c) if isinstance(c, dict) else c
        for c in raw_checks
    ]

    # 3. Run evaluator gate (includes false-fail escalation — V8)
    #    Wrapped in try/except to match monolith's error discipline:
    #    JudgeUnavailableError → loud failure (node left for human review)
    #    Generic Exception     → fail-open (emit error event, don't block)
    decision: GateDecision | None = None
    judge_error: bool = False
    gate_exc: str | None = None

    # Load previous session's L1 results for L2-gating on remediation
    prev_session = (
        s.query(NodeSession)
        .filter(
            NodeSession.run_id == ns.run_id,
            NodeSession.node_id == ns.node_id,
            NodeSession.id != ns.id,
        )
        .order_by(NodeSession.attempt.desc())
        .first()
    )
    prev_l1_passed_ids: list[str] | None = (
        prev_session.l1_passed_ids if prev_session else None
    )

    ns.verdict = verdict

    # Load partial judgments from previous re-queue (if any)
    existing_judgments = _deserialize_partial_judgments(ns.l2_partial_judgments)
    best_chunk_idx = ns.l2_best_chunk_idx or 0
    node_context_with_chunk = dict(existing_judgments=existing_judgments, best_chunk_idx=best_chunk_idx)

    try:
        decision = evaluate_gate(
            check_list=check_list,
            worktree=worktree,
            l2_fn=lambda checks, wt: run_l2(
                checks, wt, trace_id=ns.langfuse_trace_id,
                node_context=node_context_with_chunk,
                existing_judgments=existing_judgments,
            ),
            threshold=0.7,
            prev_l1_passed_ids=prev_l1_passed_ids,
            has_changes_since_prev=bool(ns.remediation_of),
            verdict=verdict,
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

    # ── Re-queue handling ────────────────────────────────────────────
    if decision is not None and decision.action == "requeue":
        _handle_requeue(s, ns, node_session_id, decision)
        return

    # ── V8 observability ──────────────────────────────────────────────
    if decision is not None and decision.l1_flagged:
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
            gate_outcome = "failed" if stop_reason != "passed" else "done"
        else:
            gate_outcome = "remediate"

    # Gap 5: Stagnation detection — N consecutive done_no_change → failed
    if gate_outcome == "remediate" and verdict == "done_no_change" and decision is not None:
        prior_no_change = (
            s.query(NodeSession)
            .filter(
                NodeSession.run_id == ns.run_id,
                NodeSession.node_id == ns.node_id,
                NodeSession.id != node_session_id,
                NodeSession.verdict == "done_no_change",
            )
            .count()
        )
        if prior_no_change >= STAGNATION_LIMIT:
            gate_outcome = "failed"
            stop_reason = "stagnation"
            if decision.goal_review is None:
                decision.goal_review = 0.0
            print(  # noqa: T201
                f"[PRINT] Evaluator: STAGNATION ns={node_session_id} "
                f"prior_no_change={prior_no_change} limit={STAGNATION_LIMIT}",
                flush=True,
            )

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

    # 8. Emit NodeSteer or NodeRemediate (steering first, then remediate)
    if gate_outcome == "remediate" and not gate_exc and not judge_error:
        steering_count = ns.steering_count or 0
        if steering_count < MAX_STEERING_ATTEMPTS:
            steer_event = NodeSteer(
                run_id=ns.run_id,
                node_id=ns.node_id,
                session_id=node_session_id,
                feedback_ref=node_session_id,
                worktree=worktree,
                steering_count=steering_count,
                ts=time.time(),
            )
            emit(s, steer_event)
        else:
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
        gate_outcome,
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


# ── Partial judgment helpers (chunked evaluation re-queue) ────────────────

def _deserialize_partial_judgments(raw: list | None) -> list:
    """Deserialize stored partial judgments back to Judgment objects.

    Returns empty list if no partial judgments exist.
    """
    if not raw:
        return []
    from backend.evaluator.schema import Judgment as JudgementModel
    result = []
    for item in raw:
        if isinstance(item, dict):
            try:
                result.append(JudgementModel(**item))
            except Exception:
                logger.warning("Skipping malformed partial judgment: %s", str(item)[:100])
    return result


def _handle_requeue(s, ns: Any, node_session_id: str,
                    decision: Any) -> None:
    """Save partial judgments and re-queue via DLX delay queue.

    Called when L2 returns ``partial=True`` (retries exhausted mid-evaluation).
    Persists completed judgments so the next delivery picks up where it
    left off, then publishes a delayed ``node.observed`` event.
    """
    import random

    completed = []
    for item in (decision.l2_feedback or []):
        if isinstance(item, dict) and item.get("check_id"):
            completed.append(item)
    ns.l2_partial_judgments = completed if completed else None
    ns.l2_best_chunk_idx = getattr(decision, "l2_chunk_idx", 0) or 0
    s.commit()

    delay_ms = random.randint(120_000, 300_000)  # 2-5 min
    logger.info(
        "REQUEUE node_session=%s partial_judgments=%d delay=%dms",
        node_session_id, len(completed), delay_ms,
    )
    print(  # noqa: T201
        f"[PRINT] Evaluator: REQUEUE ns={node_session_id} "
        f"partial={len(completed)} delay={delay_ms}ms",
        flush=True,
    )

    payload = {
        "node_session_id": node_session_id,
        "verdict": "done",
    }
    publish_ok = False
    try:
        params = pika.URLParameters(cfg.rabbit_url)
        conn = pika.BlockingConnection(params)
        ch = conn.channel()
        ch.basic_publish(
            exchange="",
            routing_key="evaluator.delay",
            body=json.dumps(payload),
            properties=pika.BasicProperties(
                delivery_mode=2,
                expiration=str(delay_ms),
            ),
        )
        conn.close()
        publish_ok = True
    except Exception as exc:
        logger.exception(
            "Failed to publish delayed retry for ns=%s: %s",
            node_session_id, exc,
        )

    # Signal consumer to skip mark_processed so the delayed copy isn't deduped.
    # Only raise when publish succeeded — without the delayed message there is
    # nothing to re-deliver, so mark_processed is correct.
    if publish_ok:
        raise RequeueHandled()


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


L4_RUNTIME_WRITABLE_DIRS = (
    "l4_scratch",
    ".venv",
    "venv",
    "node_modules",
    ".pytest_cache",
    "__pycache__",
    ".cache",
    "tmp",
    "logs",
)

L4_SOURCE_EXCLUDE_DIRS = {
    ".git",
    "l4_scratch",
    ".venv",
    "venv",
    "node_modules",
    ".pytest_cache",
    "__pycache__",
    ".cache",
    "tmp",
    "logs",
}
L4_SOURCE_EXCLUDE_FILES = {"opencode.json", ".opencode.json"}


def _l4_run_root() -> Path:
    workspace_root = Path(os.environ.get("WORKSPACE_ROOT", "/opt/aipc/conductor/workspace"))
    return workspace_root / "l4_runs"


def _manifest_install_blocks(dst: Path) -> list[tuple[str, str]]:
    """Extract (subdir, setup_command) install blocks from the project manifest.

    Reads ``.conductor/workspace.json`` (guide 03.3) — the authoritative
    layout record written by the planner with per-component ``commands``.
    Only components declaring a ``commands.setup`` contribute; each setup
    runs in the component's ``subdir`` (guide 05.9).  Returns ``[]`` when
    the manifest is missing or unparseable — assembly projects carry a
    root ``workspace.json`` with ``services`` instead and have nothing to
    install locally.
    """
    manifest_path = dst / ".conductor" / "workspace.json"
    if not manifest_path.exists():
        logger.info("No project manifest at %s — skipping L4 install", manifest_path)
        return []
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.warning("Unparseable project manifest at %s — skipping L4 install", manifest_path)
        return []
    blocks: list[tuple[str, str]] = []
    for comp in manifest.get("components", []):
        setup = ((comp.get("commands") or {}).get("setup") or "").strip()
        if not setup:
            continue
        blocks.append((comp.get("subdir", "."), setup))
    return blocks


def _write_l4_opencode_json(dst: Path, model: str | None = None) -> None:
    config: dict[str, object] = {
        "$schema": "https://opencode.ai/config.json",
        "permission": {
            "edit": {"*": "allow"},
            "bash": {
                "*": "allow",
                "rm -rf *": "deny",
                "sudo *": "deny",
                "git push *": "deny",
                "git commit *": "deny",
                "git *": "deny",
            },
            "webfetch": "deny",
            "websearch": "deny",
        },
    }
    config["model"] = model or "litellm/deepseek-planning"
    (dst / "opencode.json").write_text(json.dumps(config, indent=2) + "\n")


def _chmod_tree(root: Path, add_user_write: bool) -> None:
    for path in [root, *root.rglob("*")]:
        try:
            mode = path.stat().st_mode
            new_mode = mode | stat.S_IWUSR if add_user_write else mode & ~stat.S_IWUSR & ~stat.S_IWGRP & ~stat.S_IWOTH
            path.chmod(new_mode)
        except OSError:
            continue


def _freeze_l4_workspace(dst: Path) -> None:
    _chmod_tree(dst, add_user_write=False)
    for rel in L4_RUNTIME_WRITABLE_DIRS:
        target = dst / rel
        if target.exists():
            _chmod_tree(target, add_user_write=True)
    (dst / "l4_scratch").mkdir(exist_ok=True)
    _chmod_tree(dst / "l4_scratch", add_user_write=True)


def _cleanup_l4_workspace(dst: Path) -> None:
    if dst.exists():
        _chmod_tree(dst, add_user_write=True)
        shutil.rmtree(dst, ignore_errors=True)


def _run_l4_install_blocks(
    dst: Path,
    blocks: list[tuple[str, str]],
    timeout_s: int | None = None,
) -> list[str]:
    """Run each component's ``setup`` block in the isolated workspace.

    Every block runs in ONE shell with ``set -e`` (guide 05.1) and ``cwd``
    set to the component's ``subdir`` inside the copy (guide 05.9) — the
    manifest never relies on ``cd`` lines.  A non-zero exit raises: the L4
    agent cannot run the product, so the run must not emit bogus findings.
    """
    logs: list[str] = []
    timeout = timeout_s or int(os.environ.get("L4_INSTALL_TIMEOUT_S", "300"))
    for subdir, command in blocks:
        cwd = dst if subdir in ("", ".") else dst / subdir
        if not cwd.is_dir():
            logger.warning("L4 install subdir missing: %s — skipping setup", cwd)
            logs.append(f"[{subdir}] setup skipped (subdir missing)")
            continue
        result = subprocess.run(
            ["bash", "-lc", "set -e\n" + command],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        logs.append(f"[{subdir}] {command} -> exit {result.returncode}")
        if result.returncode != 0:
            tail = (result.stderr or result.stdout or "").strip()[-500:]
            raise RuntimeError(f"L4 setup failed for subdir {subdir!r}: {tail}")
    return logs


def _is_l4_source_file(path: Path, root: Path) -> bool:
    rel = path.relative_to(root)
    if set(rel.parts) & L4_SOURCE_EXCLUDE_DIRS:
        return False
    if path.name in L4_SOURCE_EXCLUDE_FILES:
        return False
    if path.suffix in {".pyc", ".pyo"}:
        return False
    return path.is_file()


def _l4_source_signature(root: Path) -> dict[str, str]:
    import hashlib

    sig: dict[str, str] = {}
    for path in sorted(p for p in root.rglob("*") if _is_l4_source_file(p, root)):
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        sig[path.relative_to(root).as_posix()] = h.hexdigest()
    return sig


def _verify_l4_source_unchanged(dst: Path, baseline: dict[str, str]) -> None:
    current = _l4_source_signature(dst)
    if current == baseline:
        return
    before = set(baseline)
    after = set(current)
    changed = sorted(
        (before ^ after)
        | {k for k in before & after if baseline[k] != current[k]}
    )
    raise RuntimeError(f"L4 source changed in isolated copy: {changed[:20]}")


def _load_plan_goal(db_url: str, plan_id: str) -> str | None:
    """Load the ``goal`` text from a plan for L4 scenario generation."""
    if not db_url or not plan_id:
        return None
    try:
        import psycopg
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT goal FROM plans WHERE plan_id = %s", (plan_id,))
                row = cur.fetchone()
                return row[0] if row and row[0] else None
    except Exception:
        logger.debug("Could not load plan goal for %s", plan_id)
        return None


def _is_l4_runnable(product_type: str, needs_usage_sim: bool | None) -> tuple[bool, str]:
    return True, ""


def _prepare_l4_workspace(
    run_id: str,
    worktree: str,
    install_timeout_s: int | None = None,
) -> tuple[Path, list[str], dict[str, str]]:
    src = Path(worktree).resolve()
    if not src.is_dir():
        raise FileNotFoundError(f"Run worktree not found: {src}")
    if not (src / "RUN.md").exists():
        raise FileNotFoundError(f"RUN.md not found in run worktree: {src}")
    root = _l4_run_root()
    root.mkdir(parents=True, exist_ok=True)
    dst = root / run_id
    if dst.exists():
        _cleanup_l4_workspace(dst)
    try:
        def _ignore_l4_dirs(_dir: str, names: list[str]) -> set[str]:
            return {n for n in names if n in L4_SOURCE_EXCLUDE_DIRS}

        shutil.copytree(src, dst, symlinks=True, ignore=_ignore_l4_dirs)

        # Make the workspace an independent git repo so the watcher's
        # ``_git_state_signature()`` can detect file changes.
        git_file = dst / ".git"
        if git_file.exists() and not git_file.is_dir():
            git_file.unlink()
        subprocess.run(["git", "init"], cwd=str(dst), capture_output=True, timeout=30)
        subprocess.run(["git", "add", "-A"], cwd=str(dst), capture_output=True, timeout=30)
        subprocess.run(["git", "commit", "-m", "L4 baseline"], cwd=str(dst), capture_output=True, timeout=30)

        (dst / "l4_scratch").mkdir(parents=True, exist_ok=True)
        _write_l4_opencode_json(dst)
        install_blocks = _manifest_install_blocks(dst)
        install_logs = _run_l4_install_blocks(dst, install_blocks, timeout_s=install_timeout_s)
        baseline = _l4_source_signature(dst)
        # Persist the baseline so the completion path can verify source
        # immutability in the isolated copy (guide 05, locked decision).
        (dst / "l4_scratch" / "source_baseline.json").write_text(
            json.dumps(baseline, indent=2) + "\n", encoding="utf-8",
        )
        _freeze_l4_workspace(dst)
        return dst, install_logs, baseline
    except Exception:
        _cleanup_l4_workspace(dst)
        raise


def on_run_completed(s, payload: dict) -> None:
    """Handle ``run.completed`` — run L4 two-case persona simulation.

    Spawns an l4-persona agent via AionUi ACP (reusing executor's spawn
    path) to drive the finished product as a user would.  Runs two cases:
      - Standalone: blind persona session (no plan context)
      - Acceptance: persona guided by plan.success
    Both recorded on the run.
    """
    from shared.db import session as db_session
    from shared.models import NodeSession as NodeSessionModel
    from shared.models import Run as RunModel
    import psycopg

    run_id: str = payload.get("run_id", "")
    plan_id: str = payload.get("plan_id", "")
    if not run_id:
        logger.warning("run.completed missing run_id — skipping L4")
        return

    logger.info("Run completed: run=%s plan=%s — running L4 two-case", run_id, plan_id)

    # Find the worktree from node_sessions for this run
    with db_session() as db:
        ns = db.query(NodeSessionModel).filter(
            NodeSessionModel.run_id == run_id,
            NodeSessionModel.worktree.isnot(None),
        ).first()
        if not ns or not ns.worktree:
            logger.warning("No worktree found for run %s — L4 skipped (non-runnable)", run_id)
            r = db.query(RunModel).filter(RunModel.id == run_id).first()
            if r:
                r.l4_status = "skipped_non_runnable"
                r.l4_reason = "No worktree found"
                db.commit()
            return
        worktree = ns.worktree

    # Load plan success criteria directly (ORM model is outdated)
    plan_success = ""
    needs_usage_sim: bool | None = None
    product_type = str(payload.get("product_type") or "")
    db_url = os.environ.get("DATABASE_URL", "")
    try:
        if db_url:
            with psycopg.connect(db_url) as p_conn:
                with p_conn.cursor() as cur:
                    cur.execute("SELECT success->>'text', needs_usage_sim FROM plans WHERE plan_id = %s", (plan_id,))
                    row = cur.fetchone()
                    if row:
                        if row[0]:
                            plan_success = row[0]
                        needs_usage_sim = bool(row[1]) if row[1] is not None else None
    except Exception:
        logger.debug("Could not load plan success for %s", plan_id)

    runnable, skip_reason = _is_l4_runnable(product_type, needs_usage_sim)
    if not runnable:
        with db_session() as db:
            r = db.query(RunModel).filter(RunModel.id == run_id).first()
            if r:
                r.l4_status = "skipped_non_runnable"
                r.l4_reason = skip_reason
                db.commit()
        logger.info("L4 skipped for run=%s: %s", run_id, skip_reason)
        return

    l4_workspace: Path | None = None
    ns_id: str | None = None
    try:
        l4_workspace, install_logs, source_baseline = _prepare_l4_workspace(run_id, worktree)
        logger.info("Prepared isolated L4 workspace for run=%s at %s installs=%s", run_id, l4_workspace, install_logs)

        goal = _load_plan_goal(db_url, plan_id)

        from services.evaluator.l4_runner import run_l4_phase

        ns_id = run_l4_phase(
            s=s,
            run_id=run_id,
            plan_id=plan_id,
            worktree=worktree,
            product_type=product_type,
            goal=goal,
            spec=plan_success,
            l4_workspace=l4_workspace,
        )

        logger.info("L4 spawned for run=%s ns=%s — watcher will handle completion", run_id, ns_id)
    except Exception as exc:
        logger.exception("L4 spawn failed for run=%s", run_id)
        with db_session() as db:
            r = db.query(RunModel).filter(RunModel.id == run_id).first()
            if r:
                r.l4_status = "spawn_failed"
                r.l4_reason = str(exc)[:500]
                db.commit()
        # Clean up workspace only if NodeSpawned was never emitted
        if l4_workspace is not None and ns_id is None:
            _cleanup_l4_workspace(l4_workspace)


def on_calibration_trigger(s, payload: dict) -> None:
    """Handle ``calibrate.trigger`` — run L3 calibration for a node_type."""
    from backend.evaluator.l3_calibrate import calibrate as run_calibrate

    node_type: str = payload.get("node_type", "executor")

    logger.info(
        "Calibration trigger: node_type=%s",
        node_type,
    )

    try:
        report = run_calibrate(node_type)
        logger.info(
            "Calibration for %s: trusted=%s agreement=%.4f mae=%.4f total=%d",
            node_type, report.trusted, report.agreement, report.mae, report.total,
        )
        print(  # noqa: T201
            f"[PRINT] Calibrate: node_type={node_type} "
            f"trusted={report.trusted} agreement={report.agreement:.4f} "
            f"mae={report.mae:.4f}",
            flush=True,
        )
    except Exception:
        logger.exception(
            "Calibration failed for node_type=%s", node_type,
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
        elif "node_type" in payload and "env" in payload and "ts" in payload:
            on_calibration_trigger(s, payload)
        elif payload.get("event_type") == "run.completed" or ("run_id" in payload and "plan_id" in payload and "node_session_id" not in payload):
            on_run_completed(s, payload)
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


# ── Manual L4 endpoint ────────────────────────────────────────────

class ManualL4Request(BaseModel):
    parent_run_id: str
    plan_id: str = ""
    verdict: str  # pass | partial | fail
    scenario_results: list[dict] = []
    findings: list[dict] = []
    observations: list[str] = []


class ManualL4Response(BaseModel):
    l4_run_id: str
    structural: str
    published: bool
    message: str


@app.post("/l4/manual", response_model=ManualL4Response)
def manual_l4(body: ManualL4Request) -> ManualL4Response:
    """Accept a human-authored L4 report, validate it, and publish if qualifying.

    Uses the same ``L4Report`` schema + consistency checks as the automated
    harness path.  The only difference is ``labeled_by='human'``.
    """
    from services.evaluator.l4_runner import (
        SEVERITY_RANK,
        MIN_SEVERITY_RANK,
        _should_publish,
        _validate_report,
    )
    from services.evaluator.l4_scenarios import make_spec_hash
    from shared.l4_models import Scenario

    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        return ManualL4Response(
            l4_run_id="", structural="error",
            published=False, message="DATABASE_URL not set",
        )

    l4_run_id = f"l4_manual_{uuid.uuid4().hex[:8]}"
    project_id = "manual"

    seeded = [
        Scenario(id=sr.get("scenario_id", f"s{i}"), source="seeded",
                 as_a="manual user", wants="manual scenario", success_looks_like="verified")
        for i, sr in enumerate(body.scenario_results)
    ]

    import tempfile
    worktree = Path(tempfile.mkdtemp(prefix="l4_manual_"))
    try:
        (worktree / "l4_scratch").mkdir(parents=True, exist_ok=True)

        report_data = {
            "verdict": body.verdict,
            "scenario_results": body.scenario_results,
            "findings": body.findings,
            "observations": body.observations,
        }
        report_path = worktree / "l4_scratch" / "report.json"
        report_path.write_text(json.dumps(report_data))

        structural, report = _validate_report(report_path, seeded, str(worktree))
        if structural == "ok" and report:
            spec_hash = make_spec_hash(None, None)
            with psycopg.connect(db_url) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO runs
                           (id, plan_id, project_id, state, kind, parent_run_id,
                            l4_scenarios, l4_report, l4_structural, spec_hash, merge_status)
                           VALUES (%s, %s, %s, 'completed', 'l4', %s,
                                   %s::jsonb, %s::jsonb, %s, %s, 'skipped')
                           ON CONFLICT (id) DO NOTHING""",
                        (l4_run_id, body.plan_id or "", project_id,
                         body.parent_run_id,
                         json.dumps([s.model_dump() for s in seeded]),
                         json.dumps(report_data), structural, spec_hash),
                    )
                conn.commit()

            published = False
            if _should_publish(report):
                above_floor = [
                    f for f in report.findings
                    if SEVERITY_RANK.get(f.severity, 0) >= MIN_SEVERITY_RANK
                ]
                if above_floor:
                    from contracts.events import L4Findings
                    event = L4Findings(
                        run_id=body.parent_run_id,
                        plan_id=body.plan_id,
                        project_id=project_id,
                        findings=[f.model_dump() for f in above_floor],
                        labeled_by="human",
                    )
                    routing_key = "l4.findings"
                    with psycopg.connect(db_url) as conn:
                        with conn.cursor() as cur:
                            cur.execute(
                                """INSERT INTO outbox
                                   (routing_key, payload, contracts_version)
                                   VALUES (%s, %s::jsonb, '1.0')""",
                                (routing_key, json.dumps(event.model_dump())),
                            )
                        conn.commit()
                    published = True

            return ManualL4Response(
                l4_run_id=l4_run_id,
                structural=structural,
                published=published,
                message=f"Report accepted. Published={published}",
            )
        else:
            return ManualL4Response(
                l4_run_id=l4_run_id,
                structural=structural or "unknown",
                published=False,
                message=f"Validation failed: {structural}",
            )
    finally:
        import shutil
        shutil.rmtree(worktree, ignore_errors=True)


class TriggerSystemL4Request(BaseModel):
    system_id: str
    members: list[str] | None = None


class TriggerSystemL4Response(BaseModel):
    status: str
    l4_run_id: str
    node_session_id: str
    message: str


def _sys_l4_error(message: str) -> TriggerSystemL4Response:
    return TriggerSystemL4Response(
        status="error", l4_run_id="", node_session_id="", message=message,
    )


@app.post("/l4/trigger/system", response_model=TriggerSystemL4Response)
def trigger_system_l4(body: TriggerSystemL4Request) -> TriggerSystemL4Response:
    """On-demand system L4 from the worksystem snapshot (File 10, guide 10.4).

    Gates before spawning: the system exists, no in-flight L4 run, and every
    expected member has published state (an explicit ``members`` debug subset
    is never blocked).  The L4 agent then works in an isolated worktree of
    the worksystem repo — published artifacts only, no source.
    """
    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        return _sys_l4_error("DATABASE_URL not set")

    system_id = body.system_id
    if not system_id:
        return _sys_l4_error("system_id is required")

    import psycopg

    try:
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT system_id FROM systems WHERE system_id = %s", (system_id,))
                if not cur.fetchone():
                    return _sys_l4_error(f"System {system_id} not found")
    except Exception as exc:
        logger.exception("Failed to resolve system context for %s", system_id)
        return _sys_l4_error(str(exc)[:500])

    from backend.worksystem.snapshot import (
        active_system_l4,
        blocked_result,
        missing_members,
    )

    inflight = active_system_l4(system_id)
    if inflight:
        return TriggerSystemL4Response(
            status="in_flight",
            l4_run_id=inflight["id"],
            node_session_id="",
            message=f"System L4 already running as {inflight['id']}",
        )

    missing = missing_members(system_id, members=body.members)
    if missing and not body.members:
        return TriggerSystemL4Response(**blocked_result(system_id, missing))

    from services.evaluator.l4_runner import run_system_worksystem_l4
    from shared.db import session as db_session

    try:
        with db_session() as s:
            ns_id, l4_run_id = run_system_worksystem_l4(
                s, system_id, members=body.members,
            )
    except Exception as exc:
        logger.exception("Worksystem L4 spawn failed for system=%s", system_id)
        return _sys_l4_error(f"L4 spawn failed: {str(exc)[:500]}")

    logger.info("Worksystem L4 triggered on-demand: system=%s run=%s ns=%s",
                system_id, l4_run_id, ns_id)
    return TriggerSystemL4Response(
        status="spawned",
        l4_run_id=l4_run_id,
        node_session_id=ns_id,
        message=f"L4 agent spawned for system {system_id}. Watcher will handle completion.",
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
