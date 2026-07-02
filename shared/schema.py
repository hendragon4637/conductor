"""Pydantic schema for plan DAG nodes and plans.

Exact copy of ``backend/planning/schema.py`` — used by microservices during
the monolith→microservice transition so both produce identical DAG structures.
"""

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
    """A single node in the plan DAG, matching the monolith's stored shape."""
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
    backend: str = Field(default="opencode", description="Execution backend for this node")
    agent_config_id: str = Field(default="opencode:backend-executor", description="Agent config ID")


# ── Canonical Plan ─────────────────────────────────────────

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
