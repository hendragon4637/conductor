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

# ── System L4 imports (lazy, to avoid circular dependencies in non-system flows) ──

L4_SYSTEM_SCENARIO_MODULE = "backend.assembly.system_l4"
L4_SYSTEM_HELPERS_MODULE = "backend.assembly.generator"

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


def _create_l4_node_session(db_url: str, l4_run_id: str, worktree: str, attempt: int = 1) -> str:
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
                    (ns_id, l4_run_id, "_l4_eval", "l4", "opencode", attempt, worktree, "[]"),
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


def _verify_l4_source_baseline(worktree: Path) -> bool:
    """Verify product source in the isolated copy is unchanged since prep.

    Loads ``l4_scratch/source_baseline.json`` written by
    ``_prepare_l4_workspace`` and compares it against the current tree.
    Returns ``False`` (and logs) when the source was mutated — the L4 agent
    violated the read-only contract.  A missing baseline is tolerated
    (fail-open) so legacy/pre-verification workspaces are not disrupted.
    """
    baseline_path = worktree / "l4_scratch" / "source_baseline.json"
    if not baseline_path.exists():
        logger.warning("L4 source baseline missing at %s — skipping verification", baseline_path)
        return True
    try:
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        from services.evaluator.main import _verify_l4_source_unchanged
        _verify_l4_source_unchanged(worktree, baseline)
        return True
    except RuntimeError as exc:
        logger.warning("L4 source changed in isolated copy: %s", exc)
        return False
    except Exception:
        logger.exception("L4 source verification failed for %s", worktree)
        return False


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

    # Source immutability: the L4 agent may only read product source.  If it
    # was mutated, fail the run and never publish findings from a tampered
    # artifact (guide 05, locked decision).
    if not _verify_l4_source_baseline(Path(worktree)):
        _persist_l4_failure(db_url, l4_run.id, "run_failed",
                            "L4 source mutated in isolated copy")
        _persist_l4_on_parent(db_url, l4_run.parent_run_id or "", "run_failed",
                              "L4 source mutated in isolated copy")
        _cleanup_l4_workspace(Path(worktree))
        return

    report_path = Path(worktree) / "l4_scratch" / "report.json"

    # Load scenarios from the L4 run's pre-registered scenarios
    raw_scenarios = l4_run.l4_scenarios or []
    scenarios = [Scenario(**s) if isinstance(s, dict) else s for s in raw_scenarios]

    # Validate the report
    structural, report = _validate_report(report_path, scenarios, worktree)

    project_id = l4_run.project_id or _resolve_project_id(db_url, l4_run.parent_run_id or "")
    plan_id = l4_run.plan_id or ""

    # Emit findings only when all three gates pass (guide 06.4): structure ok,
    # negative verdict, and at least one finding at/above the severity floor.
    # A parsed-but-inconsistent report never publishes.  For worksystem runs
    # (File 10) the adjustment delta is also captured: a recurring adjustment
    # escalates to a system-generated finding, and findings naming a stale
    # member are tagged so intake does not re-file an already-merged fix.
    if report is not None and structural == "ok":
        extra: list[Finding] = []
        system_id = _worksystem_system_id(worktree, l4_run)
        findings: list[Finding] = report.findings
        if system_id:
            from backend.worksystem.adjustments import (
                compute_adjustments,
                recurrence_finding,
                same_adjustment_in_last_n_runs,
                tag_possibly_stale,
            )

            adjustments = compute_adjustments(Path(worktree))
            _persist_l4_adjustments(db_url, l4_run.id, adjustments)
            if same_adjustment_in_last_n_runs(system_id, adjustments):
                sid = report.scenario_results[0].scenario_id if report.scenario_results else "s1"
                extra.append(recurrence_finding(system_id, adjustments, sid))
            findings = tag_possibly_stale(findings + extra, system_id)
        if _should_publish(report) or extra:
            try:
                _emit_l4_findings(
                    s, db_url, l4_run.parent_run_id or "", plan_id, project_id,
                    report, findings=findings,
                )
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
        # One bounded structural retry (guide 06.3): send a preamble naming the
        # defect and spawn a fresh attempt-2 session.  A second failure records
        # and continues — a structurally broken L4 never fails the parent run.
        if getattr(ns, "attempt", 1) < 2 and _retry_l4_session(
            s, db_url, ns, l4_run.id, worktree, structural
        ):
            return
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


def _retry_l4_session(
    s: Any,
    db_url: str,
    ns: Any,
    l4_run_id: str,
    worktree: str,
    structural: str,
) -> bool:
    """Send a preamble naming the structural defect and spawn attempt 2.

    Reuses the existing AionUi conversation and watcher-observed spawn path:
    a fresh ``node_session`` (attempt=2) is created and ``NodeSpawned`` is
    emitted so the watcher picks it up.  The workspace is intentionally NOT
    cleaned up — the retry agent rewrites ``l4_scratch/report.json`` in place.
    """
    conv_id = getattr(ns, "aionui_conversation_id", "") or ""
    if not conv_id:
        logger.warning("L4 retry skipped for run=%s — no conversation id on ns=%s", l4_run_id, ns.id)
        return False

    from backend.aionui.client import AionUiClient

    aionui = AionUiClient(os.environ.get("AIONUI_HOST", "http://127.0.0.1:40937"))
    preamble = (
        "Your previous report failed structural validation "
        f"(outcome: {structural}).\n"
        "Do NOT modify the product. Re-run your scenarios if needed and rewrite "
        "l4_scratch/report.json so it passes the schema, path-resolution, and "
        "consistency rules from your brief: every seeded scenario has a result, "
        "every finding references a known scenario_id with resolvable where "
        "paths, and the verdict matches the findings."
    )
    try:
        aionui.send_message(conv_id, preamble)
    except Exception:
        logger.exception("Failed to send L4 retry preamble for ns=%s", ns.id)
        return False

    ns2 = _create_l4_node_session(db_url, l4_run_id, worktree, attempt=2)
    _update_l4_node_session_conv(db_url, ns2, conv_id)
    _emit_l4_spawned(s, ns2, conv_id, worktree)
    logger.info("L4 retry spawned ns=%s (attempt=2) for run=%s", ns2, l4_run_id)
    return True


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
    findings: list[Finding] | None = None,
) -> None:
    """Emit L4Findings for qualifying findings (at or above the severity floor).

    ``findings`` overrides the report's own findings — used by the worksystem
    path to add the recurrence finding and staleness tags (File 10).
    """
    from contracts.events import L4Findings

    source = findings if findings is not None else report.findings
    above_floor = [
        Finding(
            what=f.what,
            where=f.where,
            why=f.why,
            severity=f.severity,
            scenario_id=f.scenario_id,
            possibly_stale=f.possibly_stale,
        )
        for f in source
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
    """Remove an L4 isolated workspace.

    Git worktrees (worksystem snapshots) are removed via ``git worktree
    remove`` so their metadata does not leak into the main repo; a plain
    copy is rmtree'd after making files writable.
    """
    if not dst.exists():
        return
    git_file = dst / ".git"
    if git_file.is_file():
        from backend.worksystem.repo import remove_worktree
        remove_worktree(dst)
        return
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
                        l4_scenarios, spec_hash, merge_status)
                       VALUES (%s, %s, %s, 'created', 'l4', %s, %s::jsonb, %s, 'skipped')
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


def _persist_l4_adjustments(db_url: str, l4_run_id: str, adjustments: dict[str, Any]) -> None:
    """Store the worksystem adjustment delta on the L4 run (guide 10.6)."""
    try:
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE runs SET l4_adjustments = %s::jsonb WHERE id = %s",
                    (json.dumps(adjustments), l4_run_id),
                )
            conn.commit()
    except Exception:
        logger.exception("Failed to persist L4 adjustments for %s", l4_run_id)


def _set_l4_partial_scope(db_url: str, l4_run_id: str) -> None:
    """Mark an L4 run as a debug-subset run (guide 10.7)."""
    try:
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE runs SET partial_scope = TRUE WHERE id = %s",
                    (l4_run_id,),
                )
            conn.commit()
    except Exception:
        logger.exception("Failed to set partial_scope on %s", l4_run_id)


def _worksystem_system_id(worktree: str, l4_run: Any) -> str | None:
    """The system_id when this L4 run worked in a worksystem snapshot worktree.

    Worksystem worktrees live under the worksystem ``worktrees/`` root and the
    L4 run row carries ``project_id=system_id``.  Any other L4 workspace
    (isolated source copy) returns ``None`` so the legacy path is untouched.
    """
    from backend.worksystem.repo import worktrees_root

    p = Path(worktree).resolve()
    if not p.is_relative_to(worktrees_root()):
        return None
    return l4_run.project_id or ""


def _resolve_system_plan_id(db_url: str, system_id: str) -> str | None:
    """Any plan owned by a project of the system (satisfies the runs.plan_id FK)."""
    try:
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT p.plan_id FROM plans p
                       JOIN projects pr ON pr.project_id = p.project_id
                       WHERE pr.system_id = %s
                       LIMIT 1""",
                    (system_id,),
                )
                row = cur.fetchone()
                return row[0] if row else None
    except Exception:
        return None


def _write_worksystem_opencode_json(wt: Path) -> None:
    """L4 opencode.json for a worksystem worktree.

    Edits are allowed — an edit to ``compose.yml`` is the adjustment signal
    (guide 10.6) — but git and web are denied so the agent cannot commit or
    research the components.
    """
    config = {
        "$schema": "https://opencode.ai/config.json",
        "permission": {
            "edit": {"*": "allow"},
            "bash": {
                "*": "allow",
                "git *": "deny",
                "sudo *": "deny",
                "rm -rf *": "deny",
            },
            "webfetch": "deny",
            "websearch": "deny",
        },
    }
    (wt / "opencode.json").write_text(json.dumps(config, indent=2) + "\n")


def run_system_worksystem_l4(
    s: Any,
    system_id: str,
    members: list[str] | None = None,
    aionui_host: str | None = None,
) -> tuple[str, str]:
    """Drive an on-demand system L4 from a worksystem snapshot (File 10).

    The caller has already gated: the system exists, no in-flight L4 run,
    and the worksystem is not missing members (unless ``members`` is an
    explicit debug subset).  This function:
    1. Generates cross-component scenarios from the system goal + member specs.
    2. Creates the L4 run row (``project_id=system_id``, ``kind='l4'``).
    3. Snapshots the worksystem repo into an isolated git worktree.
    4. Writes brief/scenarios/compose_urls.json/opencode.json into it.
    5. Spawns the AionUi session and emits NodeSpawned.

    Returns ``(node_session_id, l4_run_id)``.
    """
    db_url = os.environ.get("DATABASE_URL", "")
    import uuid

    from backend.assembly.system_l4 import (
        SYSTEM_WORKSYSTEM_BRIEF,
        generate_system_scenarios,
        prepare_system_l4_workspace,
    )
    from backend.worksystem.repo import ensure_worksystem
    from backend.worksystem.snapshot import (
        component_specs,
        compose_services,
        snapshot_worktree,
        staleness_notes,
        system_goal,
    )

    # 1. Scenarios from the system goal + member specs (subset-filtered)
    specs = component_specs(system_id)
    if members:
        specs = [c for c in specs if c["name"] in members]
    goal = system_goal(system_id)
    scenarios = generate_system_scenarios(goal, specs)
    spec_hash = make_spec_hash(goal or system_id, None)
    logger.info("Generated %d worksystem L4 scenarios for system=%s", len(scenarios), system_id)

    # 2. L4 run row — project_id=system_id so active_system_l4() finds it
    plan_id = _resolve_system_plan_id(db_url, system_id) or ""
    if not plan_id:
        raise RuntimeError(f"No plan found for any project of system {system_id}")
    l4_run_id = f"l4sys_{uuid.uuid4().hex[:10]}"
    _create_l4_run(db_url, l4_run_id, plan_id, system_id, "", scenarios, spec_hash)
    if members:
        _set_l4_partial_scope(db_url, l4_run_id)
    logger.info("Created worksystem L4 run=%s system=%s", l4_run_id, system_id)

    # 3. Snapshot the worksystem repo into an isolated worktree
    repo = ensure_worksystem(system_id)
    wt = snapshot_worktree(repo, l4_run_id)
    (wt / "l4_scratch").mkdir(parents=True, exist_ok=True)
    logger.info("Worksystem snapshot worktree=%s for run=%s", wt, l4_run_id)

    # 4. Brief/scenarios/compose_urls.json/opencode.json
    services = compose_services(repo)
    if members:
        services = [svc for svc in services if svc["name"] in members]
    prepare_system_l4_workspace(str(wt), services, scenarios)
    _write_worksystem_opencode_json(wt)
    notes = [n for n in staleness_notes(system_id)
             if not members or n.split(":", 1)[0].strip() in members]
    preamble = SYSTEM_WORKSYSTEM_BRIEF
    if notes:
        preamble += "\n\nSTALENESS NOTES (published vs master):\n- " + "\n- ".join(notes)

    # 5. Spawn AionUi conversation
    from backend.aionui.client import AionUiClient

    aionui = AionUiClient(aionui_host or os.environ.get("AIONUI_HOST", "http://127.0.0.1:40937"))
    l4_model = _resolve_l4_model(db_url)
    brief = render_l4_brief(l4_run_id, str(wt), scenarios, "", preamble=preamble)
    conv_id = aionui.create_conversation(
        preset_agent_type="acp",
        workspace=str(wt),
        backend="opencode",
        model=l4_model,
    )
    aionui.send_message(conv_id, brief)
    logger.info("Worksystem L4 session conv=%s worktree=%s", conv_id, wt)

    # 6. node_session + NodeSpawned
    ns_id = _create_l4_node_session(db_url, l4_run_id, str(wt))
    _update_l4_node_session_conv(db_url, ns_id, conv_id)
    _emit_l4_spawned(s, ns_id, conv_id, str(wt))

    logger.info("Worksystem L4 spawn complete: run=%s ns=%s conv=%s", l4_run_id, ns_id, conv_id)
    return ns_id, l4_run_id
