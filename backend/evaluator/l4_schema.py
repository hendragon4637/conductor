"""L4 Report schema — structural validation output for persona simulation.

L4 produces observations about the product's user-facing behavior.
This schema captures the 6-outcome verdict, per-dimension friction scores,
and structured findings for the intake pipeline.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class L4Verdict(str, Enum):
    """Six-outcome verdict from L4 structural validation.

    - ``pass_clean_structure``: all structural checks pass, no friction.
    - ``pass_minor_issues``: structure OK, minor UX friction detected.
    - ``pass_substantial_concerns``: structure OK, major UX concerns.
    - ``fail_structural_issues``: structural validation failed (missing features).
    - ``fail_incomplete``: L4 agent did not complete its analysis.
    - ``fail_error``: runtime error during L4 execution.
    """

    PASS_CLEAN_STRUCTURE = "pass_clean_structure"
    PASS_MINOR_ISSUES = "pass_minor_issues"
    PASS_SUBSTANTIAL_CONCERNS = "pass_substantial_concerns"
    FAIL_STRUCTURAL_ISSUES = "fail_structural_issues"
    FAIL_INCOMPLETE = "fail_incomplete"
    FAIL_ERROR = "fail_error"


class L4DimensionScore(BaseModel):
    """Friction score for a single UX dimension (0.0 = none, 1.0 = severe)."""

    dimension: str = Field(description="UX dimension: navigation, clarity, performance, workflow, error_handling")
    score: float = Field(ge=0.0, le=1.0, description="Friction score 0.0-1.0")
    detail: str = Field(default="", description="Human-readable observation")


class L4Finding(BaseModel):
    """A single finding from L4 persona simulation."""

    what: str = Field(description="Short description of the issue or observation")
    where: list[str] = Field(default_factory=list, description="Pages/modules/features where observed")
    why: str = Field(default="", description="Why this matters — impact on user experience")
    severity: str = Field(default="warning", description="fatal | critical | error | warning | info")
    dimension: str = Field(default="general", description="UX dimension this finding belongs to")


class L4Report(BaseModel):
    """Structured output from L4 persona simulation.

    One report per L4 run (covers both standalone and acceptance cases).
    Verdict is determined by structural validation — never by deepeval score alone.
    """

    l4_run_id: str = Field(description="ID of the L4 run (l4_<parent_run_id>)")
    parent_run_id: str = Field(description="ID of the original product run")
    plan_id: str = Field(description="Plan ID")
    project_id: str = Field(description="Project / workspace ID")

    verdict: L4Verdict = Field(description="Six-outcome verdict from structural validation")
    friction_scores: list[L4DimensionScore] = Field(default_factory=list)
    findings: list[L4Finding] = Field(default_factory=list)

    standalone_score: float | None = Field(default=None, description="deepeval score for standalone case")
    acceptance_score: float | None = Field(default=None, description="deepeval score for acceptance case")
    report_text: str = Field(default="", description="Raw L4 agent report text")

    verdict_consistent: bool = Field(
        default=True,
        description="True when all verdicts across cases agree (pass vs fail)",
    )
    created_at: float = Field(default_factory=lambda: __import__("time").time())

    def to_findings_event(self) -> dict[str, Any]:
        """Convert to the shape expected by the l4.findings event contract."""
        return {
            "run_id": self.parent_run_id,
            "plan_id": self.plan_id,
            "project_id": self.project_id,
            "findings": [f.model_dump() for f in self.findings],
            "labeled_by": "harness",
        }
