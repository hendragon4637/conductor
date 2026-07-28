from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from backend.evaluator.schema import Check


# ── Shared value objects ──────────────────────────────────────────

class NodeMember(BaseModel):
    """A team member assigned to a node, with per-member backend."""
    agent_config: str = Field(description="References agent_configs.agent_config_id")
    backend: str = Field(description="Per-member backend: opencode|opencode_omo|hermes|claude_code|codex|gemini")
    role: str = Field(default="executor", description="executor|reviewer|planner|...")


class TaskSpec(BaseModel):
    """Node-scoped assignment — what THIS node does."""
    text: str = Field(default="", description="What this node does, bounded and specific")
    inputs: list[str] = Field(default_factory=list, description="Files or artifacts this node needs")
    deliverables: list[str] = Field(default_factory=list, description="Concrete outputs it must produce")


class NodeSuccess(BaseModel):
    """Prose-only success criterion — never executed directly.
    All verifiable conditions live in checks.
    """
    text: str = Field(default="", description="Human-readable measurable success criterion")


class SuccessCriterion(BaseModel):
    """Plan-level acceptance (the integration/E2E condition).
    Prose only — all verifiable conditions live in node.checks.
    """
    text: str = Field(default="", description="Measurable whole-plan done-condition")


# ── Canonical Plan Node (v5.1 E2E spec Part 2) ───────────────────

class PlanNode(BaseModel):
    """A single node in the plan DAG.

    v5.1 canonical shape:
    - ``members``: list of NodeMember with per-member backend (required, >=1)
    - ``task``: structured TaskSpec (not a flat string)
    - ``success``: prose NodeSuccess only
    - ``checks``: all verifiable conditions (L1 deterministic + L2 rubric)
    - NO ``kind``/``ref`` (three-tier removed)
    - Node class is DERIVED from members count/backends (not stored).
    """
    id: str = Field(description="Unique node id within this plan, e.g. 'node-1'")
    members: list[NodeMember] = Field(
        min_length=1,
        description="Team members for this node with per-member backend. >=1.",
    )
    depends_on: list[str] = Field(default_factory=list)
    task: TaskSpec = Field(default_factory=lambda: TaskSpec(text=""))
    success: NodeSuccess = Field(default_factory=lambda: NodeSuccess(text=""))
    capabilities: list[str] = Field(
        default_factory=list,
        description="Capability names this node requires (populated by capability selector, post-decompose).",
    )
    checks: list[Check] = Field(
        default_factory=list,
        description="Evaluation checks (L1 deterministic + L2 rubric). Generated at decompose, ratified at approval.",
    )
    project_id: str = Field(default="default", description="Project this node belongs to")


# ── Canonical Plan (v5.1 E2E spec Part 2) ─────────────────────────

class Plan(BaseModel):
    plan_id: str
    project_id: str = "default"
    user_intent: str = Field(description="Raw human ask verbatim, 1-3 sentences")
    goal: str = Field(default="", description="Normalized one-sentence objective")
    success: SuccessCriterion = Field(
        default_factory=lambda: SuccessCriterion(text=""),
        description="Plan-level acceptance (the integration/E2E condition)",
    )
    dag: list[PlanNode] = Field(default_factory=list, description="Plan DAG: list of canonical nodes")
    ratified: bool = False
    version: int = 1
    needs_usage_sim: bool = False
    origin: str = "human"
    source_ref: str | None = None
    intake_id: int | None = None


# ── Run (execution instance) ──────────────────────────────────────

class Run(BaseModel):
    id: str
    plan_id: str
    state: str = "created"  # created|approved|running|done|failed|cancelled
    created_at: str | None = None
    approved_at: str | None = None
    finished_at: str | None = None
    worktree_root: str | None = None
    note: str | None = None

    # ── L4 MVP v1 (deprecated compat) ──────────────────────────────
    l4_standalone: float | None = None
    l4_acceptance: float | None = None
    l4_status: str | None = None
    l4_reason: str | None = None

    # ── L4 MVP v2 ──────────────────────────────────────────────────
    kind: str = "execution"          # execution | l4
    parent_run_id: str | None = None
    l4_scenarios: list[dict] | None = None
    l4_report: dict | None = None
    l4_structural: str | None = None
    spec_hash: str | None = None

    run_md_present: bool | None = None


# ── NodeSession (per-node execution record) ───────────────────────

class NodeSession(BaseModel):
    id: str
    run_id: str
    node_id: str
    members: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Snapshot of node.members at spawn time",
    )
    verdict: str | None = None   # running|stalled|quota|crashed|done
    l1_pass: bool | None = None
    goal_review: float | None = None
    gate_mode: str = "l1_l2"
    commit_tag: str | None = None
    attempt: int = 1
    aionui_team_id: str | None = None
    aionui_conversation_id: str | None = None
    langfuse_trace_id: str | None = None
    worktree: str | None = None
    created_at: str | None = None
    finished_at: str | None = None
