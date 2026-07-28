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
