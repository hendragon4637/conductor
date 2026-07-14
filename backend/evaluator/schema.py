"""Standardized Check / Judgment contract for all evaluation layers.

One schema across all domains and node types. Every check is either:
- ``deterministic`` — a shell command run in the worktree (exit 0 = pass)
- ``rubric`` — a yes/no quality question judged by the L2 LLM judge

Both types flow through the same ``NodeChecks`` container with versioning.
Every check carries a ``provenance`` tag identifying its origin:
- ``human_intent`` — derived from the ``quality_intent`` input at plan creation
- ``memory`` — recalled from Neo4j product memory
    - ``preset`` — from built-in rubric presets or deterministic logic
    - ``preset_adapted`` — from preset, but wording adjusted per-node context (LLM check-generator)
    - ``agent_default`` — from the agent_config's default_checks
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

ProvenanceType = Literal["human_intent", "memory", "preset", "preset_adapted", "agent_default"]


class OnFailTemplate(BaseModel):
    """Deterministic feedback template (no LLM) — provides what/how/evidence_from.

    These are defined in agent_config's default_checks.l1[].on_fail.
    At gate time, ``evidence`` is injected from the actual command output.
    """
    what: str = Field(description="Short description of what failed")
    how: str = Field(description="Actionable guidance on how to fix")
    evidence_from: str | None = Field(default=None, description="Source for evidence injection: stdout | path_check")


class Check(BaseModel):
    """A single evaluation check — deterministic (L1) or rubric (L2).

    Validation:
    - deterministic (L1) checks MUST have ``check_cmd``, MUST NOT have ``rubric_item``
    - rubric (L2) checks MUST have ``rubric_item``, MUST NOT have ``check_cmd``

    The ``tier`` property derives from ``type``:
    - ``"deterministic"`` → ``"L1"``
    - ``"rubric"`` → ``"L2"``

    Provenance:
    - ``human_intent`` (from quality_intent input)
    - ``memory`` (from Neo4j recall)
    - ``preset`` (from rubric presets or deterministic heuristics)
    - ``preset_adapted`` (preset wording adjusted per-node by LLM check-generator)
    - ``agent_default`` (from agent_config default_checks)
    """
    id: str = Field(description="Unique check id within the node, e.g. 'det-1', 'rubric-func-completeness'")
    type: Literal["deterministic", "rubric"]
    kind: Literal["shell", "artifact_text", "file_exists"] = Field(
        default="shell",
        description="Executor kind: shell (subprocess), artifact_text (in-process text assertion), file_exists (path check)",
    )
    criterion: str = Field(description="Human-readable intent, e.g. 'all tests pass', 'endpoint returns 200'")
    check_cmd: str | None = Field(default=None, description="Deterministic: shell command or artifact_text assertion spec (e.g. 'contains:Hello')")
    rubric_item: str | None = Field(default=None, description="Rubric: a single yes/no quality question for the L2 judge")
    weight: float = Field(default=1.0, ge=0.0, le=10.0, description="Relative importance within the node")
    provenance: ProvenanceType = Field(
        default="preset",
        description="Origin of this check: human_intent (from quality_intent input), "
                    "memory (from Neo4j recall), preset (from rubrics), or agent_default",
    )
    source_hint: str | None = Field(
        default=None,
        description="Optional context about where this check came from, "
                    "e.g. 'from quality_intent: money must be integer cents'",
    )
    on_fail: OnFailTemplate | None = Field(
        default=None,
        description="L1 deterministic feedback template (what/how/evidence_from). "
                    "Used at gate time to produce structured L1 feedback.",
    )

    @property
    def tier(self) -> str:
        """Derive evaluation tier from check type.

        ``"deterministic"`` → ``"L1"``, ``"rubric"`` → ``"L2"``.
        This aligns the data model with the spec's ``tier`` terminology
        without duplicating the field.
        """
        return "L1" if self.type == "deterministic" else "L2"

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
        if self.type == "deterministic" and self.kind not in ("shell", "artifact_text", "file_exists"):
            raise ValueError(
                f"Check {self.id}: unsupported deterministic kind '{self.kind}'"
            )
        if self.kind == "file_exists" and self.check_cmd and not self.check_cmd.startswith("test -f "):
            # allow raw path as fallback — runner will construct the test
            pass
        return self


class Judgment(BaseModel):
    """What the L2 judge returns PER rubric check.

    Stored alongside the check result for auditability.
    """
    check_id: str
    criteria_met: bool = Field(description="True = this particular rubric item was satisfied (GEval score >= threshold)")
    score: float = Field(default=0.0, ge=0.0, le=1.0, description="Continuous GEval score (0.0-1.0) for weighted averaging")
    explanation: str = Field(description="Free-text rationale for the judgment")
    feedback_raw: dict | None = Field(
        default=None,
        description="Structured feedback {what, where, why, how} parsed from GEval reason. "
                    "Set by l2_judge.run_l2(); consumed by gate._j_to_dict() for remediation brief.",
    )


class NodeChecks(BaseModel):
    """Container for all checks attached to a single node.

    Versioning (Eval Ops): when the ratchet later edits a rubric,
    it bumps ``checks_version``.  Never silently mutate ratified checks.
    """
    node_id: str
    checks: list[Check] = Field(default_factory=list)
    checks_version: int = Field(default=1, ge=1, description="Bumped on rubric edit by ratchet")
