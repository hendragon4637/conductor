"""SQLAlchemy ORM models — single source of truth for all table schemas.

Ownership convention (enforced by review):
- WRITER: <service-name>  — only the owning service writes; others read.
"""

from __future__ import annotations

from sqlalchemy import Boolean, Column, Float, ForeignKey, Integer, JSON, String, Text, DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import declarative_base
from sqlalchemy.sql import func

# REAL type removed from sqlalchemy 2.0 top-level; use Float instead
Real = Float

Base = declarative_base()


# ── Outbox (File 01) — WRITER: any service that publishes events ────────────

class Outbox(Base):
    __tablename__ = "outbox"
    id = Column(Integer, primary_key=True, autoincrement=True)
    routing_key = Column(String, nullable=False)
    payload = Column(JSON, nullable=False)
    contracts_version = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    published_at = Column(DateTime(timezone=True), nullable=True)


class ProcessedEvent(Base):
    __tablename__ = "processed_events"
    consumer = Column(String, primary_key=True)
    event_key = Column(String, primary_key=True)
    processed_at = Column(DateTime(timezone=True), server_default=func.now())


# ── Plans — WRITER: planner-svc ─────────────────────────────────────────────

class Plan(Base):
    __tablename__ = "plans"
    plan_id = Column(String, primary_key=True)
    project_id = Column(String, nullable=False)
    session_id = Column(String, nullable=True)
    user_intent = Column(Text, nullable=True)
    status = Column(String, nullable=False, default="draft")
    ratified = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


# ── Runs — WRITER: executor-svc ─────────────────────────────────────────────

class Run(Base):
    __tablename__ = "runs"
    id = Column(String, primary_key=True)
    plan_id = Column(String, ForeignKey("plans.plan_id"), nullable=False)
    project_id = Column(String, nullable=False)
    state = Column(String, nullable=False, default="created")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    approved_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    worktree_root = Column(String, nullable=True)
    note = Column(Text, nullable=True)
    worktree_status = Column(String, nullable=False, default="active")
    merge_commit = Column(String, nullable=True)
    quarantine_tag = Column(String, nullable=True)
    worktree_expires_at = Column(DateTime(timezone=True), nullable=True)

    # ── Run completion outcomes (guide 06.1) — independent of run outcome ──
    merge_status = Column(String, nullable=False, default="merged")   # merged | blocked | skipped
    merge_ref = Column(String, nullable=True)
    merge_error = Column(Text, nullable=True)
    image_status = Column(String, nullable=False, default="skipped")  # built | failed | skipped
    image_tag = Column(String, nullable=True)
    image_error = Column(Text, nullable=True)
    publish_status = Column(String, nullable=False, default="skipped")  # published | stale | skipped
    publish_error = Column(Text, nullable=True)
    publish_commit = Column(String, nullable=True)

    # ── L4 MVP v1 (deprecated, kept for backward compat) ────────────
    l4_standalone = Column(Real, nullable=True)
    l4_acceptance = Column(Real, nullable=True)
    l4_status = Column(String, nullable=True)
    l4_reason = Column(Text, nullable=True)

    # ── L4 MVP v2 ──────────────────────────────────────────────────
    kind = Column(String, nullable=False, default="execution")   # execution | l4
    parent_run_id = Column(String, nullable=True)                # for kind='l4': the run being critiqued
    l4_scenarios = Column(JSON, nullable=True)                  # seeded scenarios (input)
    l4_report = Column(JSON, nullable=True)                     # validated report (output)
    l4_structural = Column(String, nullable=True)               # ok | missing_file | parse_error | schema_error | path_error | inconsistent
    spec_hash = Column(String, nullable=True)                   # hash(goal+spec), future reuse

    run_md_present = Column(Boolean, nullable=True)

    dep_shas = Column(JSON, nullable=False, default={})


# ── Node Sessions — WRITER: executor-svc (spawn), watcher-svc (verdict), evaluator-svc (gate) ──

class NodeSession(Base):
    __tablename__ = "node_sessions"
    id = Column(String, primary_key=True)
    run_id = Column(String, ForeignKey("runs.id"), nullable=False)
    node_id = Column(String, nullable=False)
    backend = Column(String, nullable=False)
    worktree = Column(String, nullable=True)
    verdict = Column(String, nullable=True)
    l1_pass = Column(Boolean, nullable=True)
    goal_review = Column(Real, nullable=True)
    commit_tag = Column(String, nullable=True)
    attempt = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    finished_at = Column(DateTime(timezone=True), nullable=True)
    members = Column(JSON, nullable=False, default=list)
    gate_mode = Column(String, nullable=False, default="l1_l2")
    aionui_team_id = Column(String, nullable=True)
    aionui_conversation_id = Column(String, nullable=True)
    langfuse_trace_id = Column(String, nullable=True)
    remediation_of = Column(String, ForeignKey("node_sessions.id"), nullable=True)
    feedback = Column(JSON, nullable=True)
    fail_reason = Column(String, nullable=True)
    parent_node_id = Column(String, nullable=True)
    depth = Column(Integer, nullable=False, default=0)
    superseded_by = Column(String, nullable=True)
    l1_flagged = Column(Boolean, nullable=False, default=False)
    l1_feedback = Column(JSON, nullable=True)
    l1_passed_ids = Column(JSON, nullable=False, default=list)
    l2_passed = Column(Boolean, nullable=True)
    l2_score = Column(Real, nullable=True)
    l2_feedback = Column(JSON, nullable=True)
    gate_outcome = Column(String, nullable=True)
    best_score = Column(Real, nullable=True)
    stop_reason = Column(String, nullable=True)
    role = Column(String, nullable=False, default="execution")
    steering_count = Column(Integer, nullable=False, default=0)
    l2_partial_judgments = Column(JSON, nullable=True)
    """Per-rubric-item judgments persisted mid-evaluation for crash recovery.
    On re-delivery, items with saved judgments are skipped."""
    l2_best_chunk_idx = Column(Integer, nullable=True)
    """Chunk index that most recently passed a rubric item.
    Used on re-delivery to try the best-known chunk first."""


# ── System layer — WRITER: planner-svc (system goal), shared (helpers) ──────

import re
import uuid as _uuid


def _slug(name: str) -> str:
    """Produce a lowercase, hyphen-separated, filesystem-safe slug."""
    s = name.lower().strip()
    s = re.sub(r"[^a-z0-9\-]", "-", s)
    s = re.sub(r"-+", "-", s)
    s = s.strip("-")
    return s or "unnamed"


def create_project(
    system_id: str,
    name: str,
    kind: str = "component",
    domain: str | None = None,
    description: str | None = None,
) -> str:
    """Create a new project with a derived project_id.

    Args:
        system_id: Parent system.
        name: Human-facing project name (must be unique within system).
        kind: ``component`` or ``assembly``.
        domain: Domain for purpose/standard selection.
        description: Optional one-line summary.

    Returns:
        The new project_id.

    Raises:
        ValueError: If the name is not unique within the system.
    """
    import os
    import psycopg
    from psycopg.rows import dict_row

    db_url = os.environ.get("DATABASE_URL", "")
    project_id = f"{system_id}-{_slug(name)}"

    with psycopg.connect(db_url, row_factory=dict_row) as c:
        with c.cursor() as cur:
            # Check uniqueness within system
            cur.execute(
                "SELECT project_id FROM projects WHERE system_id = %s AND name = %s",
                (system_id, name),
            )
            existing = cur.fetchone()
            if existing:
                raise ValueError(
                    f"Project name '{name}' already exists in system '{system_id}' "
                    f"as {existing['project_id']}"
                )

            # Collision fallback with existing derived id
            if project_id:
                cur.execute(
                    "SELECT project_id FROM projects WHERE project_id = %s",
                    (project_id,),
                )
                if cur.fetchone():
                    project_id = f"{system_id}-{_slug(name)}-{_uuid.uuid4().hex[:4]}"

            # Inherit persona_id from system
            cur.execute(
                "SELECT persona_id FROM systems WHERE system_id = %s",
                (system_id,),
            )
            sys_row = cur.fetchone()
            persona_id = sys_row["persona_id"] if sys_row else "default"

            cur.execute(
                """INSERT INTO projects
                   (project_id, system_id, name, kind, persona_id, status,
                    description, repo_path, created_at, updated_at)
                   VALUES (%s, %s, %s, %s, %s, 'active', %s, %s, now(), now())
                   RETURNING project_id""",
                (
                    project_id,
                    system_id,
                    name,
                    kind,
                    persona_id,
                    description or "",
                    f"{system_id}/{_slug(name)}",
                ),
            )
            row = cur.fetchone()
            id_ = row["project_id"] if row else project_id
        c.commit()

    return id_


def add_dependency(
    project_id: str,
    depends_on_project_id: str,
    dep_name: str | None = None,
) -> None:
    """Add a dependency edge between two projects in the same system.

    Args:
        project_id: The dependent project.
        depends_on_project_id: The project it depends on.
        dep_name: Directory name under ``deps/`` (defaults to dependency's project name).

    Raises:
        ValueError: If projects are in different systems, or the edge creates a cycle.
    """
    import os
    import psycopg
    from psycopg.rows import dict_row

    db_url = os.environ.get("DATABASE_URL", "")

    with psycopg.connect(db_url, row_factory=dict_row) as c:
        with c.cursor() as cur:
            # Same-system enforcement
            cur.execute(
                "SELECT system_id FROM projects WHERE project_id IN (%s, %s)",
                (project_id, depends_on_project_id),
            )
            rows = cur.fetchall()
            if len(rows) < 2:
                raise ValueError(f"One or both projects not found: {project_id}, {depends_on_project_id}")
            sys_a, sys_b = rows[0]["system_id"], rows[1]["system_id"]
            if sys_a != sys_b:
                raise ValueError(
                    f"Dependencies must stay within a system: "
                    f"{project_id} (system={sys_a}) → {depends_on_project_id} (system={sys_b})"
                )

            # Cycle detection via DFS
            cur.execute(
                """SELECT project_id, depends_on_project_id FROM project_dependencies
                   WHERE project_id IN (SELECT project_id FROM projects WHERE system_id = %s)
                      OR depends_on_project_id IN (SELECT project_id FROM projects WHERE system_id = %s)""",
                (sys_a, sys_a),
            )
            all_edges = cur.fetchall()

            adj: dict[str, list[str]] = {}
            for edge in all_edges:
                adj.setdefault(edge["project_id"], []).append(edge["depends_on_project_id"])
            adj.setdefault(project_id, []).append(depends_on_project_id)

            visited: set[str] = set()
            stack: set[str] = set()

            def _dfs(nid: str) -> None:
                if nid in stack:
                    raise ValueError(
                        f"Dependency cycle detected involving project {nid}. "
                        f"Path: {project_id} → {depends_on_project_id}"
                    )
                if nid in visited:
                    return
                visited.add(nid)
                stack.add(nid)
                for neighbor in adj.get(nid, []):
                    _dfs(neighbor)
                stack.remove(nid)

            for nid in list(adj.keys()):
                if nid not in visited:
                    _dfs(nid)

            # Resolve dep_name from dependency's project name
            if not dep_name:
                cur.execute(
                    "SELECT name FROM projects WHERE project_id = %s",
                    (depends_on_project_id,),
                )
                dep_row = cur.fetchone()
                dep_name = dep_row["name"] if dep_row else depends_on_project_id

            cur.execute(
                """INSERT INTO project_dependencies
                   (project_id, depends_on_project_id, dep_name, created_at)
                   VALUES (%s, %s, %s, now())
                   ON CONFLICT (project_id, depends_on_project_id) DO NOTHING""",
                (project_id, depends_on_project_id, dep_name),
            )
        c.commit()


# ── SQLAlchemy ORM models for system layer ────────────────────────────────


class System(Base):
    __tablename__ = "systems"
    system_id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    description = Column(String, nullable=True)
    glossary = Column(JSON, nullable=False, default={})
    persona_id = Column(String, nullable=False, default="default")
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class ProjectDependency(Base):
    __tablename__ = "project_dependencies"
    project_id = Column(String, primary_key=True)
    depends_on_project_id = Column(String, primary_key=True)
    dep_name = Column(String, nullable=False)
    consumed_by = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class PendingGoal(Base):
    __tablename__ = "pending_goals"
    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(String, nullable=False)
    raw_input = Column(String, nullable=False)
    origin = Column(String, nullable=False, default="system_goal")
    wait_for = Column(JSON, nullable=False, default=[])
    status = Column(String, nullable=False, default="pending")
    plan_id = Column(String, nullable=True)
    last_error = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())


class SystemProposal(Base):
    __tablename__ = "system_proposals"
    id = Column(Integer, primary_key=True, autoincrement=True)
    raw_input = Column(String, nullable=False)
    proposal = Column(JSON, nullable=False)
    edited = Column(JSON, nullable=True)
    status = Column(String, nullable=False, default="proposed")
    system_id = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
