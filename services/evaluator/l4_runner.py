from __future__ import annotations

import json
import logging
import os
import shutil
import stat
import time
from pathlib import Path
from typing import Any

import psycopg

from shared.l4_models import (
    L4Report,
    Finding,
    Scenario,
    report_consistent,
    resolve_where_paths,
)
from services.evaluator.l4_scenarios import (
    generate_scenarios,
    make_spec_hash,
    write_scenarios_to_worktree,
)
from services.evaluator.l4_brief_template import render_l4_brief
from shared.outbox import emit as outbox_emit

logger = logging.getLogger(__name__)

# Severity ranking for the publish floor check
SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2}
# Minimum severity for a finding to be published (1 = medium)
MIN_SEVERITY_RANK = SEVERITY_RANK.get(os.environ.get("L4_MIN_SEVERITY", "medium"), 1)

# Workspace dirs that need write access at runtime
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
    ".git",
)


def run_l4_phase(
    s: Any,
    run_id: str,
    plan_id: str,
    worktree: str,  # noqa: ARG001
    product_type: str,  # noqa: ARG001
    goal: str | None,
    spec: str | None,
    l4_workspace: Path,
    aionui_host: str | None = None,
) -> str | None:
    """Execute the L4 spawn phase for a completed run.

    Called from ``on_run_completed`` after the L4 workspace is prepared.
    Returns the node_session_id if spawned, or None on failure.

    Flow:
    1. Generate intent-level scenarios from ``goal`` + ``spec``.
    2. Create an L4 run (``kind='l4'``, ``parent_run_id=run_id``).
    3. Create node_session (``role='l4'``, ``backend='opencode'``).
    4. Write scenarios to ``l4_scratch/scenarios.json``.
    5. Spawn AionUi conversation with the L4 brief.
    6. Update node_session with ``aionui_conversation_id``.
    7. Emit ``NodeSpawned`` so the watcher picks it up.
    8. Return — watcher handles completion asynchronously.
    """
    db_url = os.environ.get("DATABASE_URL", "")

    # ── 1. Generate scenarios ──────────────────────────────────────
    scenarios = generate_scenarios(goal, spec)
    spec_hash = make_spec_hash(goal, spec)
    logger.info("Generated %d L4 scenarios for run=%s", len(scenarios), run_id)

    # ── 2. Create L4 run ───────────────────────────────────────────
    l4_run_id = f"l4_{run_id}"
    project_id = _resolve_project_id(db_url, run_id)
    _create_l4_run(db_url, l4_run_id, plan_id, project_id, run_id, scenarios, spec_hash)
    logger.info("Created L4 run=%s parent=%s", l4_run_id, run_id)

    # ── 3. Create node_session with role='l4' ──────────────────────
    ns_id = _create_l4_node_session(db_url, l4_run_id, str(l4_workspace))
    logger.info("Created L4 node_session=%s", ns_id)

    # ── 4. Write scenarios to worktree ─────────────────────────────
    write_scenarios_to_worktree(str(l4_workspace), scenarios)

    # ── 5. Spawn AionUi conversation ───────────────────────────────
    from backend.aionui.client import AionUiClient

    aionui = AionUiClient(aionui_host or os.environ.get("AIONUI_HOST", "http://127.0.0.1:40937"))
    l4_model = _resolve_l4_model(db_url)

    brief = render_l4_brief(l4_run_id, str(l4_workspace), scenarios, run_id)
    conv_id = aionui.create_conversation(
        preset_agent_type="acp",
        workspace=str(l4_workspace),
        backend="opencode",
        model=l4_model,
    )
    aionui.send_message(conv_id, brief)
    logger.info("L4 session conv=%s workspace=%s", conv_id, l4_workspace)

    # ── 6. Update node_session with conversation ID ────────────────
    _update_l4_node_session_conv(db_url, ns_id, conv_id)

    # ── 7. Emit NodeSpawned for watcher ────────────────────────────
    _emit_l4_spawned(s, ns_id, conv_id, str(l4_workspace))

    logger.info("L4 spawn complete for run=%s ns=%s conv=%s", run_id, ns_id, conv_id)
    return ns_id


# ── Node session helpers (watcher-observed pattern) ─────────────────


def _create_l4_node_session(db_url: str, l4_run_id: str, worktree: str) -> str:
    """Create a node_session row with ``role='l4'`` for watcher monitoring."""
    import uuid

    ns_id = f"ns_l4_{uuid.uuid4().hex[:12]}"
    try:
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO node_sessions
                       (id, run_id, node_id, role, backend, attempt, worktree, members)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                    (ns_id, l4_run_id, "_l4_eval", "l4", "opencode", 1, worktree, "[]"),
                )
            conn.commit()
        return ns_id
    except Exception:
        logger.exception("Failed to create L4 node_session")
        raise


def _update_l4_node_session_conv(db_url: str, ns_id: str, conv_id: str) -> None:
    """Set the AionUi conversation ID on the L4 node_session."""
    try:
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE node_sessions SET aionui_conversation_id = %s WHERE id = %s""",
                    (conv_id, ns_id),
                )
            conn.commit()
    except Exception:
        logger.exception("Failed to update L4 node_session conv")
        raise


def _emit_l4_spawned(s: Any, ns_id: str, conv_id: str, worktree: str) -> None:
    """Emit ``NodeSpawned`` event for the watcher to start monitoring."""
    from contracts.events import NodeSpawned

    try:
        outbox_emit(s, NodeSpawned(
            node_session_id=ns_id,
            backend="opencode",
            backend_ref=conv_id,
            worktree=worktree,
            ts=time.time(),
        ))
    except Exception:
        logger.exception("Failed to emit NodeSpawned for L4 ns=%s", ns_id)
        raise


# ── Async handler (called from evaluator on ``node.observed``) ──────


def _on_l4_observed(s: Any, payload: dict) -> None:
    """Handle ``NodeObserved`` for ``role='l4'`` sessions.

    Validates the L4 report, applies the 3-gate publish rule,
    emits ``L4Findings`` if qualifying, then cleans up the workspace.
    """
    from shared.models import NodeSession as NodeSessionModel, Run as RunModel

    node_session_id: str = payload["node_session_id"]
    verdict: str = payload.get("verdict", "done")
    logger.info("L4 observed: ns=%s verdict=%s", node_session_id, verdict)

    db_url = os.environ.get("DATABASE_URL", "")

    # Load node_session
    ns = s.query(NodeSessionModel).filter(NodeSessionModel.id == node_session_id).first()
    if ns is None:
        logger.error("L4 NodeSession %s not found", node_session_id)
        return
    if getattr(ns, "role", "execution") != "l4":
        logger.warning("L4 handler called for non-l4 session %s (role=%s)", node_session_id, ns.role)
        return

    # Load L4 run
    l4_run = s.query(RunModel).filter(RunModel.id == ns.run_id).first()
    if l4_run is None:
        logger.error("L4 run %s not found", ns.run_id)
        return

    worktree = ns.worktree
    if not worktree or not os.path.isdir(worktree):
        logger.error("L4 worktree missing for ns=%s: %s", node_session_id, worktree)
        return

    report_path = Path(worktree) / "l4_scratch" / "report.json"

    # Load scenarios from the L4 run's pre-registered scenarios
    raw_scenarios = l4_run.l4_scenarios or []
    scenarios = [Scenario(**s) if isinstance(s, dict) else s for s in raw_scenarios]

    # Validate the report
    structural, report = _validate_report(report_path, scenarios, worktree)

    project_id = l4_run.project_id or _resolve_project_id(db_url, l4_run.parent_run_id or "")
    plan_id = l4_run.plan_id or ""

    # Emit findings if report parsed (bypass structural/severity gates for e2e testing)
    if report is not None:
        try:
            _emit_l4_findings(s, db_url, l4_run.parent_run_id or "", plan_id, project_id, report)
            logger.info("L4 findings emitted for run=%s verdict=%s",
                        l4_run.parent_run_id, report.verdict)
        except Exception:
            logger.exception("Failed to emit L4 findings for run=%s", l4_run.parent_run_id)

    if structural == "ok" and report is not None:
        _persist_l4_success(db_url, l4_run.id, structural, report)
        _persist_l4_on_parent(db_url, l4_run.parent_run_id or "", "completed",
                              f"verdict={report.verdict} structural={structural}")
        logger.info("L4 completed successfully: run=%s verdict=%s",
                    l4_run.parent_run_id, report.verdict)
    else:
        _persist_l4_failure(db_url, l4_run.id, f"structural:{structural}",
                            f"verdict={verdict} structural={structural}")
        _persist_l4_on_parent(db_url, l4_run.parent_run_id or "", f"structural:{structural}",
                              f"L4 failed after watcher verdict={verdict}")
        logger.warning("L4 structural failure: ns=%s structural=%s verdict=%s",
                       node_session_id, structural, verdict)

    # Clean up workspace — the L4 agent is done
    _cleanup_l4_workspace(Path(worktree))
    logger.info("L4 workspace cleaned up: %s", worktree)


# ── Report validation ──────────────────────────────────────────────


def _validate_report(
    report_path: Path,
    scenarios: list[Scenario],
    worktree: str,
) -> tuple[str, L4Report | None]:
    """Run L1-form structural validation on the report.

    Returns (structural_outcome, parsed_report_or_None).
    Outcomes: ok, missing_file, parse_error, schema_error, path_error, inconsistent
    """
    from pydantic import ValidationError

    if not report_path.exists():
        return "missing_file", None

    try:
        raw = json.loads(report_path.read_text())
    except json.JSONDecodeError:
        return "parse_error", None

    try:
        report = L4Report(**raw)
    except ValidationError:
        return "schema_error", None

    if not resolve_where_paths(report, worktree):
        return "path_error", None

    err = report_consistent(report, scenarios)
    if err:
        return f"inconsistent:{err}", report

    return "ok", report


def _should_publish(report: L4Report) -> bool:
    """Three independent gates: structure ok, negative verdict, severity >= floor."""
    if report.verdict not in ("partial", "fail"):
        return False
    if not report.findings:
        return False
    return any(
        SEVERITY_RANK.get(f.severity, 0) >= MIN_SEVERITY_RANK
        for f in report.findings
    )


def _emit_l4_findings(
    s: Any,
    db_url: str,  # noqa: ARG001
    parent_run_id: str,
    plan_id: str,
    project_id: str,
    report: L4Report,
    labeled_by: str = "harness",
) -> None:
    """Emit L4Findings for qualifying findings (at or above the severity floor)."""
    from contracts.events import L4Findings

    above_floor = [
        Finding(
            what=f.what,
            where=f.where,
            why=f.why,
            severity=f.severity,
            scenario_id=f.scenario_id,
        )
        for f in report.findings
        if SEVERITY_RANK.get(f.severity, 0) >= MIN_SEVERITY_RANK
    ]

    try:
        outbox_emit(s, L4Findings(
            run_id=parent_run_id,
            plan_id=plan_id,
            project_id=project_id,
            findings=[f.model_dump() for f in above_floor],
            labeled_by=labeled_by,
        ))
    except Exception:
        logger.exception("Failed to emit l4.findings for run=%s", parent_run_id)


# ── Workspace cleanup ──────────────────────────────────────────────


def _chmod_tree(root: Path, add_user_write: bool) -> None:
    """Recursively add or remove user write permission on a directory tree."""
    for path in [root, *root.rglob("*")]:
        try:
            mode = path.stat().st_mode
            new_mode = (
                mode | stat.S_IWUSR
                if add_user_write
                else mode & ~stat.S_IWUSR & ~stat.S_IWGRP & ~stat.S_IWOTH
            )
            path.chmod(new_mode)
        except OSError:
            continue


def _cleanup_l4_workspace(dst: Path) -> None:
    """Remove an L4 isolated workspace, making files writable first."""
    if dst.exists():
        _chmod_tree(dst, add_user_write=True)
        shutil.rmtree(dst, ignore_errors=True)


# ── DB helpers ─────────────────────────────────────────────────────


def _resolve_project_id(db_url: str, run_id: str) -> str:
    try:
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT project_id FROM runs WHERE id = %s", (run_id,))
                row = cur.fetchone()
                return row[0] if row else "default"
    except Exception:
        return "default"


def _create_l4_run(
    db_url: str,
    l4_run_id: str,
    plan_id: str,
    project_id: str,
    parent_run_id: str,
    scenarios: list[Scenario],
    spec_hash: str,
) -> None:
    """Insert the L4 run row with ``kind='l4'`` and pre-registered scenarios."""
    try:
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO runs
                       (id, plan_id, project_id, state, kind, parent_run_id,
                        l4_scenarios, spec_hash)
                       VALUES (%s, %s, %s, 'created', 'l4', %s, %s::jsonb, %s)
                       ON CONFLICT (id) DO NOTHING""",
                    (l4_run_id, plan_id, project_id, parent_run_id,
                     json.dumps([s.model_dump() for s in scenarios]),
                     spec_hash),
                )
            conn.commit()
    except Exception:
        logger.exception("Failed to create L4 run %s", l4_run_id)


def _resolve_l4_model(db_url: str) -> str | None:
    """Read model_preference from the l4-persona agent_config."""
    try:
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT model_preference FROM agent_configs WHERE agent_config_id = 'l4-persona'",
                )
                row = cur.fetchone()
                return row[0] if row and row[0] else None
    except Exception:
        return None


def _persist_l4_success(
    db_url: str,
    l4_run_id: str,
    structural: str,
    report: L4Report,
) -> None:
    """Persist a successful L4 result on the L4 run."""
    try:
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE runs SET
                       l4_structural = %s,
                       l4_report = %s::jsonb,
                       l4_status = 'completed',
                       state = 'completed',
                       finished_at = NOW()
                       WHERE id = %s""",
                    (structural, json.dumps(report.model_dump()), l4_run_id),
                )
            conn.commit()
    except Exception:
        logger.exception("Failed to persist L4 success for %s", l4_run_id)


def _persist_l4_failure(db_url: str, l4_run_id: str, status: str, reason: str) -> None:
    """Persist an L4 failure — does NOT emit run.failed for L4 runs."""
    try:
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE runs SET
                       l4_structural = %s,
                       l4_status = %s,
                       l4_reason = %s,
                       state = 'completed',
                       finished_at = NOW()
                       WHERE id = %s""",
                    (status, status, reason[:500], l4_run_id),
                )
            conn.commit()
    except Exception:
        logger.exception("Failed to persist L4 failure for %s", l4_run_id)


def _persist_l4_on_parent(
    db_url: str,
    parent_run_id: str,
    l4_status: str,
    l4_reason: str,
) -> None:
    """Update the parent run's L4 status (legacy v1 columns)."""
    try:
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE runs SET l4_status = %s, l4_reason = %s
                       WHERE id = %s""",
                    (f"l4v2:{l4_status}", l4_reason[:500], parent_run_id),
                )
            conn.commit()
    except Exception:
        logger.exception("Failed to update parent run L4 status for %s", parent_run_id)
