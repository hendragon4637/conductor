"""Decomposed plan spec — chunk-level plan nodes.

Extends File 17 PlanNode with execution metadata.
Every chunk node = an AionUi team led by the built-in orchestrator.
The node specifies only team members (specialist agent_configs) + dependencies.
The orchestrator is IMPLICIT (always present, not stored on the node).

Supersedes the three-tier ``kind`` field from Files 17-18 (tool/single_agent/team).
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from backend.evaluator.schema import Check, NodeChecks
from backend.planning.schema import SuccessCriterion, NodeSuccess, TaskSpec


class ChunkNode(BaseModel):
    """A chunk in the decomposed plan DAG.

    Every node = an AionUi team led by the built-in orchestrator.
    The node specifies only the specialist members (agent_configs) + node dependencies.
    The orchestrator is implicit (the fixed built-in AionUi agent) and never stored here.

    A node with 1 member is still team = orchestrator + 1 member (no special path).
    """
    id: str = Field(description="Unique chunk id within this decomposed plan")
    members: list[str] = Field(
        description="Specialist agent_config_ids for this node (>=1). "
                    "The built-in orchestrator is implicit and NOT included here.",
        min_length=1,
    )
    depends_on: list[str] = Field(
        default_factory=list,
        description="Node IDs this node depends on (must resolve to existing chunks)",
    )
    success: NodeSuccess = Field(
        default_factory=lambda: NodeSuccess(text=""),
    )
    checks: list[Check] = Field(
        default_factory=list,
        description="Evaluation checks for this node (deterministic + rubric). "
                    "Generated at decompose time, MUST be ratified by human at plan approval.",
    )
    worktree_strategy: Literal["shared_sequential", "separate_parallel"] = Field(
        default="shared_sequential",
        description="shared_sequential: one worktree for dependent chunks. "
                    "separate_parallel: each chunk gets its own worktree.",
    )
    commit_on_done: bool = Field(
        default=True,
        description="This chunk is a commit boundary — Conductor creates a git tag after completion",
    )
    regression_required: bool = Field(
        default=True,
        description="Accumulated test suite must pass before advancing (v1: watcher verdict gate)",
    )
    retry_policy: dict[str, Any] = Field(
        default_factory=lambda: {"max": 2, "backoff_s": 30},
        description="Bounded retry policy: max attempts, backoff in seconds",
    )
    gate_mode: str = Field(
        default="watcher_only",
        description="v1: watcher verdict only. Future: 'test_cmd', 'reviewer', 'evaluator_gate'",
    )

    @model_validator(mode="after")
    def _validate_members(self) -> "ChunkNode":
        if len(self.members) < 1:
            raise ValueError(
                f"Chunk {self.id}: must have at least one member "
                "(the built-in orchestrator is implicit)"
            )
        return self


class DecomposedPlan(BaseModel):
    """A validated decomposed plan with chunk-level granularity.

    Validation rules:
    - Acyclic; deps resolve; topological order computable
    - Every chunk has >=1 member
    - shared_sequential chunks map to ONE worktree
    - Every non-first chunk has a regression check
    - No backward edges (iteration via retry_policy)
    - checks_ratified must be True before plan can be approved
    """
    plan_id: str
    worktree_root: str = Field(
        default="/opt/aipc/conductor/workspace",
        description="Root directory for worktrees",
    )
    chunks: list[ChunkNode]
    checks_ratified: bool = Field(
        default=False,
        description="Human has reviewed and approved the generated checks. "
                    "Plan cannot be approved until True.",
    )

    @model_validator(mode="after")
    def _validate_topology(self) -> "DecomposedPlan":
        """Validate DAG acyclicity and dependency resolution."""
        chunk_ids = {c.id for c in self.chunks}
        for c in self.chunks:
            for dep in c.depends_on:
                if dep not in chunk_ids:
                    raise ValueError(
                        f"Chunk {c.id} depends on {dep} which does not exist. "
                        f"Available: {chunk_ids}"
                    )
        if not _is_dag_acyclic(self.chunks):
            raise ValueError("Decomposed plan DAG contains a cycle")
        return self


def _is_dag_acyclic(chunks: list[ChunkNode]) -> bool:
    adj: dict[str, list[str]] = {c.id: list(c.depends_on) for c in chunks}
    visited: dict[str, int] = {}

    def _dfs(nid: str) -> bool:
        if nid in visited:
            return visited[nid] == 2
        visited[nid] = 1
        for dep in adj.get(nid, []):
            if dep in visited and visited[dep] == 1:
                return False
            if dep not in visited:
                if not _dfs(dep):
                    return False
        visited[nid] = 2
        return True

    for nid in adj:
        if nid not in visited:
            if not _dfs(nid):
                return False
    return True


def validate_decomposed(
    dplan: DecomposedPlan,
) -> None:
    """Additional validation beyond Pydantic model validation.

    Raises ValueError on first failure.
    """
    # Every chunk has >=1 member (enforced by Pydantic)
    for c in dplan.chunks:
        if len(c.members) < 1:
            raise ValueError(
                f"Chunk {c.id}: must have at least one member "
                "(the built-in orchestrator is implicit)"
            )

    # shared_sequential chunks share one worktree
    seq_chunks = [c for c in dplan.chunks if c.worktree_strategy == "shared_sequential"]
    if len(seq_chunks) >= 2:
        worktrees = {f"{dplan.worktree_root}/{c.id.split('-')[0]}" for c in seq_chunks}
        if len(worktrees) > 1:
            raise ValueError(
                "shared_sequential chunks must share ONE worktree "
                f"but found {len(worktrees)}: {worktrees}"
            )

    # Every non-first chunk has a regression check (via L1 shell check or rubric item)
    if len(dplan.chunks) >= 2:
        for c in dplan.chunks[1:]:
            regression_found = False
            for chk in c.checks:
                text = (chk.check_cmd or chk.rubric_item or chk.criterion or "").lower()
                if any(kw in text for kw in ("prior", "regression", "pass")):
                    regression_found = True
                    break
            if not regression_found and c.regression_required:
                raise ValueError(
                    f"Chunk {c.id}: non-first chunk missing regression check. "
                    f"Add an L1 or L2 check mentioning 'prior', 'regression', or 'pass'."
                )
