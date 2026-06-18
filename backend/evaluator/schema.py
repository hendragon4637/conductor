"""Standardized Check / Judgment contract for all evaluation layers.

One schema across all domains and node types. Every check is either:
- ``deterministic`` — a shell command run in the worktree (exit 0 = pass)
- ``rubric`` — a yes/no quality question judged by the L2 LLM judge

Both types flow through the same ``NodeChecks`` container with versioning.
Every check carries a ``provenance`` tag identifying its origin:
- ``human_intent`` — derived from the ``quality_intent`` input at plan creation
- ``memory`` — recalled from Neo4j product memory
- ``preset`` — from built-in rubric presets or deterministic logic
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

ProvenanceType = Literal["human_intent", "memory", "preset"]


class Check(BaseModel):
    """A single evaluation check — deterministic or rubric.

    Validation:
    - deterministic checks MUST have ``check_cmd``, MUST NOT have ``rubric_item``
    - rubric checks MUST have ``rubric_item``, MUST NOT have ``check_cmd``

    Provenance:
    - ``human_intent`` (from quality_intent input)
    - ``memory`` (from Neo4j recall)
    - ``preset`` (from rubric presets or deterministic heuristics)
    """
    id: str = Field(description="Unique check id within the node, e.g. 'det-1', 'rubric-func-completeness'")
    type: Literal["deterministic", "rubric"]
    criterion: str = Field(description="Human-readable intent, e.g. 'all tests pass', 'endpoint returns 200'")
    check_cmd: str | None = Field(default=None, description="Deterministic: shell command run in worktree; exit 0 = pass")
    rubric_item: str | None = Field(default=None, description="Rubric: a single yes/no quality question for the L2 judge")
    weight: float = Field(default=1.0, ge=0.0, le=10.0, description="Relative importance within the node")
    provenance: ProvenanceType = Field(
        default="preset",
        description="Origin of this check: human_intent (from quality_intent input), "
                    "memory (from Neo4j recall), or preset (from rubrics)",
    )
    source_hint: str | None = Field(
        default=None,
        description="Optional context about where this check came from, "
                    "e.g. 'from quality_intent: money must be integer cents'",
    )

    @model_validator(mode="after")
    def _validate_type_fields(self) -> "Check":
        if self.type == "deterministic" and not self.check_cmd:
            raise ValueError(
                f"Check {self.id}: deterministic type requires 'check_cmd'"
            )
        if self.type == "deterministic" and self.rubric_item:
            raise ValueError(
                f"Check {self.id}: deterministic type must not have 'rubric_item'"
            )
        if self.type == "rubric" and not self.rubric_item:
            raise ValueError(
                f"Check {self.id}: rubric type requires 'rubric_item'"
            )
        if self.type == "rubric" and self.check_cmd:
            raise ValueError(
                f"Check {self.id}: rubric type must not have 'check_cmd'"
            )
        return self


class Judgment(BaseModel):
    """What the L2 judge returns PER rubric check.

    Stored alongside the check result for auditability.
    """
    check_id: str
    criteria_met: bool = Field(description="True = this particular rubric item was satisfied")
    explanation: str = Field(description="Free-text rationale for the judgment")


class NodeChecks(BaseModel):
    """Container for all checks attached to a single node.

    Versioning (Eval Ops): when the ratchet later edits a rubric,
    it bumps ``checks_version``.  Never silently mutate ratified checks.
    """
    node_id: str
    checks: list[Check] = Field(default_factory=list)
    checks_version: int = Field(default=1, ge=1, description="Bumped on rubric edit by ratchet")
