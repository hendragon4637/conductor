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
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel

from contracts.events import GateEvaluated, NodeSteer, NodeRemediate, CalibrateTrigger
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
        steering_count = ns.steering_count or 0
        if steering_count < 5:
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
    if getattr(ns, "role", "execution") == "planning":
        logger.info("NodeSession %s is role=planning — evaluator skips", node_session_id)
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
    try:
        decision = evaluate_gate(
            check_list=check_list,
            worktree=worktree,
            l2_fn=lambda checks, wt: run_l2(
                checks, wt, trace_id=ns.langfuse_trace_id,
            ),
            threshold=0.7,
            prev_l1_passed_ids=prev_l1_passed_ids,
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

    # 8. Emit NodeSteer or NodeRemediate (steering first, then remediate)
    if gate_outcome == "remediate" and not gate_exc and not judge_error:
        steering_count = ns.steering_count or 0
        if steering_count < 5:
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

L4_INSTALL_MARKERS = (
    "python -m venv",
    "python3 -m venv",
    "uv venv",
    "pip install",
    "python -m pip install",
    "python3 -m pip install",
    ".venv/bin/pip install",
    "venv/bin/pip install",
    "uv pip install",
    "npm install",
    "npm ci",
    "pnpm install",
    "yarn install",
    "bun install",
    "poetry install",
    "go mod download",
    "cargo fetch",
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


def _strip_shell_prompt(line: str) -> str:
    stripped = line.strip()
    if stripped.startswith(('-', '*')):
        stripped = stripped[1:].strip()
    for prefix in ("$ ", "> "):
        if stripped.startswith(prefix):
            return stripped[len(prefix):].strip()
    return stripped


def _is_install_command(candidate: str) -> bool:
    lowered = candidate.lower().strip()
    return lowered.startswith((
        "python -m venv ",
        "python3 -m venv ",
        "uv venv",
        "pip install ",
        "python -m pip install ",
        "python3 -m pip install ",
        ".venv/bin/pip install ",
        "venv/bin/pip install ",
        "uv pip install ",
        "npm install",
        "npm ci",
        "pnpm install",
        "yarn install",
        "bun install",
        "poetry install",
        "go mod download",
        "cargo fetch",
    ))


def _parse_l4_install_commands(run_md: Path) -> list[str]:
    if not run_md.exists():
        return []
    commands: list[str] = []
    in_fence = False
    for raw in run_md.read_text(errors="replace").splitlines():
        line = raw.strip()
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if not line or line.startswith("#"):
            continue
        candidate = _strip_shell_prompt(line)
        lowered = candidate.lower()
        if _is_install_command(candidate):
            commands.append(candidate)
        elif in_fence and any(marker in lowered for marker in L4_INSTALL_MARKERS):
            commands.append(candidate)
    deduped: list[str] = []
    seen: set[str] = set()
    for command in commands:
        if command not in seen:
            deduped.append(command)
            seen.add(command)
    return deduped


def _write_l4_opencode_json(dst: Path) -> None:
    config = {
        "$schema": "https://opencode.ai/config.json",
        "permission": {
            "edit": {"*": "deny", "l4_scratch/**": "allow"},
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


def _run_l4_install_commands(dst: Path, commands: list[str], timeout_s: int | None = None) -> list[str]:
    logs: list[str] = []
    for command in commands:
        parts = command.split()
        if len(parts) >= 4 and parts[:3] in (["python", "-m", "venv"], ["python3", "-m", "venv"]):
            venv_dir = dst / parts[3]
            if venv_dir.exists():
                shutil.rmtree(venv_dir, ignore_errors=True)
        result = subprocess.run(
            command,
            cwd=str(dst),
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout_s or int(os.environ.get("L4_INSTALL_TIMEOUT_S", "300")),
            check=False,
        )
        logs.append(f"{command} -> {result.returncode}")
        if result.returncode != 0:
            raise RuntimeError(
                f"L4 install failed for {command}: {(result.stderr or result.stdout)[-500:]}"
            )
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


def _is_l4_runnable(product_type: str, needs_usage_sim: bool | None) -> tuple[bool, str]:
    normalized = (product_type or "").strip().lower()
    if normalized in {"doc", "docs", "none", "static"}:
        return False, f"product_type={product_type!r} has no runnable surface"
    if needs_usage_sim is False and not normalized:
        return False, "plan.needs_usage_sim is false and no runnable product_type supplied"
    return True, ""


def _l4_goal_brief(run_id: str, case_name: str, product_type: str, plan_success: str) -> str:
    acceptance = ""
    if case_name == "acceptance":
        acceptance = (
            "\nAcceptance focus:\n"
            f"- Verify this success criterion as pass/fail: {plan_success or '(none provided)'}\n"
        )
    scenario = plan_success or "Use the product exactly as RUN.md documents."
    return (
        f"L4 persona simulation — Case: {case_name}\n\n"
        f"Run ID: {run_id}\n"
        f"Product type: {product_type or 'unknown'}\n"
        f"Scenario: {scenario}\n"
        f"{acceptance}\n"
        "Instructions (mandatory):\n"
        "1. Read RUN.md only to learn exact run/verify commands and product access details.\n"
        "2. Run the product using RUN.md commands; dependencies are already installed by Conductor.\n"
        "3. Exercise the scenario as a black-box user and report what worked/failed.\n"
        "4. DO NOT modify, fix, patch, refactor, or edit product source files.\n"
        "5. DO NOT inspect source code to diagnose bugs; observe behavior through the running product.\n"
        "6. Write any notes, logs, screenshots, or scratch files only under l4_scratch/.\n"
        "7. Return a structured L4 report with per-step PASS/FAIL, status/output, and overall friction.\n"
    )


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
        shutil.copytree(src, dst, symlinks=True)
        (dst / "l4_scratch").mkdir(parents=True, exist_ok=True)
        _write_l4_opencode_json(dst)
        install_commands = _parse_l4_install_commands(dst / "RUN.md")
        install_logs = _run_l4_install_commands(dst, install_commands, timeout_s=install_timeout_s)
        baseline = _l4_source_signature(dst)
        _freeze_l4_workspace(dst)
        return dst, install_logs, baseline
    except Exception:
        _cleanup_l4_workspace(dst)
        raise


def _l4_score_via_deepeval(report_text: str, task_description: str) -> float:
    """Score L4 report using deepeval TaskCompletionMetric.

    Replaces the heuristic keyword-counting approach with an LLM judge
    that evaluates whether the report demonstrates task completion.
    """
    from deepeval.metrics import TaskCompletionMetric
    from deepeval.test_case import LLMTestCase
    from shared.eval_models import JUDGE as JUDGE_MODEL

    try:
        metric = TaskCompletionMetric(
            task=task_description,
            model=JUDGE_MODEL,
            threshold=0.5,
            include_reason=True,
        )
        test_case = LLMTestCase(
            input=task_description,
            actual_output=report_text,
        )
        metric.measure(test_case)
        return round(float(metric.score), 4)
    except Exception as exc:
        logger.warning("L4 TaskCompletionMetric failed: %s", exc)
        return 0.5  # neutral fallback


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
    try:
        db_url = os.environ.get("DATABASE_URL", "")
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
    try:
        l4_workspace, install_logs, source_baseline = _prepare_l4_workspace(run_id, worktree)
        logger.info("Prepared isolated L4 workspace for run=%s at %s installs=%s", run_id, l4_workspace, install_logs)

        l4_standalone: float | None = None
        l4_acceptance: float | None = None
        case_notes: list[str] = []
        timed_out = False

        from backend.aionui.client import AionUiClient
        aionui = AionUiClient(os.environ.get("AIONUI_HOST", "http://127.0.0.1:40937"))

        for case_name in ("standalone", "acceptance"):
            goal_brief = _l4_goal_brief(run_id, case_name, product_type, plan_success)
            conv_id = aionui.create_conversation(
                preset_agent_type="acp",
                workspace=str(l4_workspace),
                backend="opencode",
            )
            aionui.send_message(conv_id, goal_brief)
            logger.info("L4 case=%s conv=%s workspace=%s", case_name, conv_id, l4_workspace)

            deadline = time.monotonic() + int(os.environ.get("L4_CASE_TIMEOUT_S", "300"))
            l4_text = "L4 agent timed out"
            while time.monotonic() < deadline:
                time.sleep(10)
                try:
                    msgs = aionui.get_messages(conv_id)
                    assistant_responses = [
                        m for m in (msgs or [])
                        if m.get("type") == "text" and m.get("position") == "left"
                    ]
                    if not assistant_responses:
                        continue
                    raw_content = assistant_responses[-1].get("content", "{}")
                    try:
                        parsed = json.loads(raw_content) if isinstance(raw_content, str) else raw_content
                        l4_text = parsed.get("content", str(raw_content)) if isinstance(parsed, dict) else str(raw_content)
                    except (json.JSONDecodeError, TypeError):
                        l4_text = str(raw_content)
                    score = _l4_score_via_deepeval(l4_text, goal_brief)
                    if case_name == "standalone":
                        l4_standalone = score
                    else:
                        l4_acceptance = score
                    case_notes.append(f"{case_name}: conv={conv_id} score={score}")
                    print(f"[PRINT] L4 case={case_name} score={score} conv={conv_id}", flush=True)
                    break
                except Exception as exc:
                    logger.debug("L4 poll failed for conv=%s: %s", conv_id, exc)
            else:
                timed_out = True
                case_notes.append(f"{case_name}: conv={conv_id} timeout")
                break

        _verify_l4_source_unchanged(l4_workspace, source_baseline)

        with db_session() as db:
            r = db.query(RunModel).filter(RunModel.id == run_id).first()
            if r:
                r.l4_standalone = l4_standalone
                r.l4_acceptance = l4_acceptance
                r.l4_status = "run_failed" if timed_out else "scored"
                r.l4_reason = "; ".join(case_notes)
                db.commit()

        logger.info(
            "L4 done for run=%s status=%s standalone=%s acceptance=%s",
            run_id, "run_failed" if timed_out else "scored", l4_standalone, l4_acceptance,
        )
    except Exception as exc:
        logger.exception("L4 isolated execution failed for run=%s", run_id)
        with db_session() as db:
            r = db.query(RunModel).filter(RunModel.id == run_id).first()
            if r:
                r.l4_status = "run_failed"
                r.l4_reason = str(exc)[:500]
                db.commit()
    finally:
        if l4_workspace is not None:
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
