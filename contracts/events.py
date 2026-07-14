"""Event payload models shared across all Conductor services.

Every service publishes and consumes events through these models — they are the
single source of truth for message shapes on the RabbitMQ ``conductor.events``
topic exchange.

Payload rule enforced by the models: only IDs + tiny fields + ``*_ref``.
No full diffs/feedback bodies — those stay in the DB, fetched by id.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


# ── Event payloads ──────────────────────────────────────────────────────────

class PlanRatified(BaseModel):
    plan_id: str
    run_id: str
    project_id: str
    env: str
    ts: float


class NodeDispatch(BaseModel):
    run_id: str
    node_id: str
    node_session_id: str
    attempt: int
    worktree: str
    env: str
    ts: float


class NodeSpawned(BaseModel):
    node_session_id: str
    backend: str
    backend_ref: str
    worktree: str
    ts: float


class NodeObserved(BaseModel):
    node_session_id: str
    verdict: str
    fs_changed: bool
    ts: float


class GateEvaluated(BaseModel):
    node_session_id: str
    run_id: str
    node_id: str
    gate_outcome: str
    l1_pass: Optional[bool] = None
    l2_score: Optional[float] = None
    best_score: Optional[float] = None
    feedback_ref: Optional[str] = None
    ts: float


class NodeRemediate(BaseModel):
    run_id: str
    node_id: str
    prev_session_id: str
    attempt_next: int
    feedback_ref: str
    worktree: str
    ts: float


class RunCompleted(BaseModel):
    run_id: str
    plan_id: str
    status: str
    worktree_status: str
    ts: float


class RunFailed(BaseModel):
    run_id: Optional[str] = None
    reason: str
    quarantine_tag: Optional[str] = None
    ts: float


class PlanAwaitingClarification(BaseModel):
    plan_id: str
    questions: list[str]
    ts: float


class RatchetTrigger(BaseModel):
    agent_config_id: str
    node_type: str
    env: str
    ts: float


class NodeSteer(BaseModel):
    """Reuse the existing AionUi session and send a fix-forward message.

    Emitted by the evaluator when ``steering_count < 5``, consumed by
    executor-svc (``_handle_node_steer``) to call ``send_message`` on the
    existing conversation instead of spawning a brand new team/remediation.
    """
    run_id: str
    node_id: str
    session_id: str       # current node_session (has aionui_conversation_id to reuse)
    feedback_ref: str     # session with evaluator feedback
    worktree: str
    steering_count: int   # current count; handler sets steering_count+1 on the new session
    ts: float


class CalibrateTrigger(BaseModel):
    node_type: str
    env: str
    ts: float


# ── Routing key map ─────────────────────────────────────────────────────────

ROUTING: dict[type[BaseModel], str] = {
    PlanRatified: "plan.ratified",
    NodeDispatch: "node.dispatch",
    NodeSpawned: "node.spawned",
    NodeObserved: "node.observed",
    GateEvaluated: "gate.evaluated",
    NodeSteer: "node.steer",
    NodeRemediate: "node.remediate",
    RunCompleted: "run.completed",
    RunFailed: "run.failed",
    PlanAwaitingClarification: "plan.awaiting_clarification",
    RatchetTrigger: "ratchet.trigger",
    CalibrateTrigger: "calibrate.trigger",
}
