from __future__ import annotations

import json
import logging
from typing import Any

import psycopg
from psycopg.rows import dict_row

from backend.planning.schema import Plan

logger = logging.getLogger(__name__)


def _get_db() -> str:
    import os
    return os.environ["DATABASE_URL"]


# ── Plan persistence ──────────────────────────────────────────────

def save_plan(plan: Plan, ratified: bool = False) -> None:
    """Persist a plan to the database (insert or update).

    v5.1: stores goal + success JSONB; no session_id.
    v7.1: auto-creates the project row if the project_id doesn't exist yet,
          preventing FK violations when /goal receives a new project_id.

    Args:
        plan: The plan object to persist.
        ratified: Whether the plan's checks/spec have been ratified.
    """
    db_url = _get_db()
    dag_json = json.dumps([n.model_dump() for n in plan.dag])
    project_id = plan.project_id or (plan.dag[0].project_id if plan.dag else "default")
    success_json = plan.success.model_dump() if hasattr(plan.success, 'model_dump') else {"text": str(plan.success)}

    needs_usage_sim = getattr(plan, "needs_usage_sim", False)
    with psycopg.connect(db_url) as c:
        with c.cursor() as cur:
            cur.execute(
                """INSERT INTO projects (project_id, name, repo_path)
                   VALUES (%s, %s, %s)
                   ON CONFLICT (project_id) DO NOTHING""",
                (project_id, project_id, f"/opt/aipc/conductor/workspace/{project_id}"),
            )
            cur.execute(
                """INSERT INTO plans
                   (plan_id, project_id, user_intent, goal, success, dag, ratified, version, needs_usage_sim)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (plan_id) DO UPDATE SET
                     project_id = EXCLUDED.project_id,
                     user_intent = EXCLUDED.user_intent,
                     goal = EXCLUDED.goal,
                     success = EXCLUDED.success,
                     dag = EXCLUDED.dag,
                     ratified = EXCLUDED.ratified,
                     needs_usage_sim = EXCLUDED.needs_usage_sim
                """,
                (
                    plan.plan_id,
                    project_id,
                    plan.user_intent,
                    plan.goal,
                    json.dumps(success_json),
                    dag_json,
                    ratified,
                    plan.version,
                    needs_usage_sim,
                ),
            )
        c.commit()


def set_ratified(plan_id: str) -> None:
    """Mark a plan as ratified (checks/spec approved by human)."""
    db_url = _get_db()
    with psycopg.connect(db_url) as c:
        with c.cursor() as cur:
            cur.execute(
                "UPDATE plans SET ratified = TRUE WHERE plan_id = %s",
                (plan_id,),
            )
        c.commit()


def update_plan_gate_result(
    plan_id: str,
    plan_goal_review: float,
    l2_judgments: list[dict[str, Any]],
    hard_failures: list[dict[str, Any]],
    raw_response: str | None = None,
) -> None:
    """Persist plan-level evaluator score, per-item L2 judgments, and raw LLM response."""
    db_url = _get_db()
    with psycopg.connect(db_url) as c:
        with c.cursor() as cur:
            cur.execute(
                """UPDATE plans
                      SET plan_goal_review = %s,
                          plan_l2_judgments = %s::jsonb,
                          plan_l2_hard_failures = %s::jsonb,
                          plan_l2_raw_response = %s
                    WHERE plan_id = %s
                """,
                (
                    plan_goal_review,
                    json.dumps(l2_judgments),
                    json.dumps(hard_failures),
                    raw_response,
                    plan_id,
                ),
            )
        c.commit()


def get_plan(plan_id: str) -> dict[str, Any] | None:
    """Load a plan dict from the database."""
    db_url = _get_db()
    with psycopg.connect(db_url, row_factory=dict_row) as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT * FROM plans WHERE plan_id = %s", (plan_id,),
            )
            row = cur.fetchone()
    if row:
        row["dag"] = json.loads(row["dag"]) if isinstance(row["dag"], str) else row["dag"]
        if isinstance(row.get("multimodal_refs"), str):
            row["multimodal_refs"] = json.loads(row["multimodal_refs"])
    return row


# ── Run persistence ───────────────────────────────────────────────

def save_run(run: dict[str, Any]) -> None:
    """Insert or update a run record (defense-in-depth: checks active-run limit)."""
    project_id = run.get("project_id")
    if not project_id:
        raise ValueError("save_run requires project_id")

    db_url = _get_db()
    with psycopg.connect(db_url) as c:
        # Defense-in-depth: reject if project already has an active run
        _check_active_project_run(c, project_id)

        with c.cursor() as cur:
            cur.execute(
                """INSERT INTO runs
                   (id, plan_id, project_id, state, worktree_root, note,
                    approved_at, finished_at,
                    l4_standalone, l4_acceptance, l4_status, l4_reason, run_md_present)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (id) DO UPDATE SET
                     state = EXCLUDED.state,
                     project_id = COALESCE(EXCLUDED.project_id, runs.project_id),
                     worktree_root = COALESCE(EXCLUDED.worktree_root, runs.worktree_root),
                     approved_at = COALESCE(EXCLUDED.approved_at, runs.approved_at),
                     finished_at = COALESCE(EXCLUDED.finished_at, runs.finished_at),
                     note = COALESCE(EXCLUDED.note, runs.note),
                     l4_standalone = COALESCE(EXCLUDED.l4_standalone, runs.l4_standalone),
                     l4_acceptance = COALESCE(EXCLUDED.l4_acceptance, runs.l4_acceptance),
                     l4_status = COALESCE(EXCLUDED.l4_status, runs.l4_status),
                     l4_reason = COALESCE(EXCLUDED.l4_reason, runs.l4_reason),
                     run_md_present = COALESCE(EXCLUDED.run_md_present, runs.run_md_present)
                """,
                (
                    run.get("id"),
                    run.get("plan_id"),
                    project_id,
                    run.get("state", "created"),
                    run.get("worktree_root"),
                    run.get("note"),
                    run.get("approved_at"),
                    run.get("finished_at"),
                    run.get("l4_standalone"),
                    run.get("l4_acceptance"),
                    run.get("l4_status"),
                    run.get("l4_reason"),
                    run.get("run_md_present"),
                ),
            )
        c.commit()

    # ── Stamp run with domain standard after successful insert ──
    plan_id = run.get("plan_id")
    run_id = run.get("id")
    if plan_id and run_id:
        try:
            from backend.standards.seeder import stamp_run_standard

            with psycopg.connect(db_url) as c:
                with c.cursor() as cur:
                    cur.execute(
                        "SELECT goal FROM plans WHERE plan_id = %s",
                        (plan_id,),
                    )
                    row = cur.fetchone()

            if row and row[0] is not None:
                goal = row[0]
                domain = None
                if isinstance(goal, str):
                    try:
                        parsed = json.loads(goal)
                        if isinstance(parsed, dict):
                            domains = parsed.get("domains")
                            domain = domains[0] if domains else parsed.get("domain")
                    except (json.JSONDecodeError, TypeError):
                        pass
                elif isinstance(goal, dict):
                    domains = goal.get("domains")
                    domain = domains[0] if domains else goal.get("domain")

                if domain:
                    stamp_run_standard(run_id, domain, db_url)
        except Exception:
            logger.exception("Failed to stamp run %s with standard", run_id)


def _check_active_project_run(conn: psycopg.Connection, project_id: str) -> None:
    """Raise ``ValueError`` if the project already has an active (non-terminal) run.

    Called by ``save_run`` and the planner endpoints as defense-in-depth.
    The partial unique index ``idx_runs_active_project`` provides DB-level
    enforcement; this app-layer check gives a clean error message.
    """
    with conn.cursor() as cur:
        cur.execute(
            """SELECT id FROM runs
               WHERE project_id = %s
                 AND state NOT IN ('done', 'failed', 'cancelled')
               LIMIT 1""",
            (project_id,),
        )
        existing = cur.fetchone()
    if existing:
        raise ValueError(
            f"Project {project_id} already has an active run ({existing[0]}). "
            "Complete or cancel it before starting a new one."
        )


def get_active_run_for_project(project_id: str) -> dict[str, Any] | None:
    """Return an active (non-terminal) run for the project, or None.

    Used by the ``/goal`` and ``/ratify`` endpoints to check early
    before any expensive processing.
    """
    db_url = _get_db()
    with psycopg.connect(db_url, row_factory=dict_row) as c:
        with c.cursor() as cur:
            cur.execute(
                """SELECT id, plan_id, state, project_id FROM runs
                   WHERE project_id = %s
                     AND state NOT IN ('done', 'failed', 'cancelled')
                   LIMIT 1""",
                (project_id,),
            )
            return cur.fetchone()


def get_run(run_id: str) -> dict[str, Any] | None:
    """Load a run dict from the database."""
    db_url = _get_db()
    with psycopg.connect(db_url, row_factory=dict_row) as c:
        with c.cursor() as cur:
            cur.execute("SELECT * FROM runs WHERE id = %s", (run_id,))
            return cur.fetchone()


def list_runs(plan_id: str | None = None) -> list[dict[str, Any]]:
    """List runs, optionally filtered by plan_id."""
    db_url = _get_db()
    with psycopg.connect(db_url, row_factory=dict_row) as c:
        with c.cursor() as cur:
            if plan_id:
                cur.execute(
                    "SELECT * FROM runs WHERE plan_id = %s ORDER BY created_at DESC",
                    (plan_id,),
                )
            else:
                cur.execute("SELECT * FROM runs ORDER BY created_at DESC")
            return cur.fetchall()


def update_run_state(run_id: str, state: str) -> None:
    """Transition a run's state."""
    db_url = _get_db()
    with psycopg.connect(db_url) as c:
        with c.cursor() as cur:
            finished_fields = ""
            if state in ("done", "failed", "cancelled"):
                finished_fields = ", finished_at = NOW()"
            elif state == "approved":
                finished_fields = ", approved_at = NOW()"
            cur.execute(
                f"UPDATE runs SET state = %s{finished_fields} WHERE id = %s",
                (state, run_id),
            )
        c.commit()


# ── NodeSession persistence ──────────────────────────────────────

def save_node_session(ns: dict[str, Any]) -> None:
    """Insert or update a node_session record (v5.1: includes members, gate_mode, aionui fields)."""
    db_url = _get_db()
    members_json = json.dumps(ns.get("members", [])) if ns.get("members") else None
    with psycopg.connect(db_url) as c:
        with c.cursor() as cur:
            cur.execute(
                """INSERT INTO node_sessions
                   (id, run_id, node_id, backend, members, gate_mode,
                    worktree, verdict, l1_pass, goal_review, commit_tag, attempt,
                    aionui_team_id, aionui_conversation_id, langfuse_trace_id, finished_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (id) DO UPDATE SET
                     verdict = COALESCE(EXCLUDED.verdict, node_sessions.verdict),
                     l1_pass = COALESCE(EXCLUDED.l1_pass, node_sessions.l1_pass),
                     goal_review = COALESCE(EXCLUDED.goal_review, node_sessions.goal_review),
                     commit_tag = COALESCE(EXCLUDED.commit_tag, node_sessions.commit_tag),
                     attempt = EXCLUDED.attempt,
                     members = COALESCE(EXCLUDED.members, node_sessions.members),
                     gate_mode = COALESCE(EXCLUDED.gate_mode, node_sessions.gate_mode),
                     aionui_team_id = COALESCE(EXCLUDED.aionui_team_id, node_sessions.aionui_team_id),
                     aionui_conversation_id = COALESCE(EXCLUDED.aionui_conversation_id, node_sessions.aionui_conversation_id),
                     langfuse_trace_id = COALESCE(EXCLUDED.langfuse_trace_id, node_sessions.langfuse_trace_id),
                     finished_at = COALESCE(EXCLUDED.finished_at, node_sessions.finished_at)
                """,
                (
                    ns.get("id"),
                    ns.get("run_id"),
                    ns.get("node_id"),
                    ns.get("backend"),
                    members_json,
                    ns.get("gate_mode", "l1_l2"),
                    ns.get("worktree"),
                    ns.get("verdict"),
                    ns.get("l1_pass"),
                    ns.get("goal_review"),
                    ns.get("commit_tag"),
                    ns.get("attempt", 1),
                    ns.get("aionui_team_id"),
                    ns.get("aionui_conversation_id"),
                    ns.get("langfuse_trace_id"),
                    ns.get("finished_at"),
                ),
            )
        c.commit()


def get_node_sessions(run_id: str) -> list[dict[str, Any]]:
    """Get all node_sessions for a run."""
    db_url = _get_db()
    with psycopg.connect(db_url, row_factory=dict_row) as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT * FROM node_sessions WHERE run_id = %s ORDER BY created_at",
                (run_id,),
            )
            return cur.fetchall()
