from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field


# ── Scenario (input) ───────────────────────────────────────────────

class Scenario(BaseModel):
    """An intent-level scenario for the L4 agent to attempt.

    Generated from goal+spec ONLY, before the agent has seen the repo.
    No steps — the agent must figure out HOW to do each scenario from
    RUN.md.  If it can't, that IS the finding.
    """

    id: str
    source: Literal["seeded", "adhoc"] = "seeded"
    as_a: str = Field(..., min_length=3, description="User role / persona")
    wants: str = Field(..., min_length=5, description="What the user wants to do")
    success_looks_like: str = Field(..., min_length=5, description="What success looks like")


# ── Report types (output) ──────────────────────────────────────────

class ScenarioResult(BaseModel):
    scenario_id: str
    attempted: list[str] = Field(min_length=1, description="What the agent actually did")
    outcome: Literal["pass", "fail", "blocked"]
    notes: str = ""


class Finding(BaseModel):
    what: str = Field(min_length=10)
    where: list[str] = Field(min_length=1, description="Repo-relative paths that must resolve")
    why: str = Field(min_length=10, description="Observed evidence, not speculation")
    severity: Literal["low", "medium", "high"]
    scenario_id: str = Field(..., description="Every finding must trace to a scenario attempt")
    possibly_stale: bool = Field(
        default=False,
        description="True when the finding names a member whose published state is "
        "behind master (File 10) — intake must not re-file a fix already on master",
    )


class L4Report(BaseModel):
    """Structured output from an L4 session.

    - ``findings`` = things that should change (the ONLY array intake consumes).
    - ``observations`` = confirmations, praise, notes (persisted, never routed).
    - Every finding must have a valid ``severity``, resolving ``where`` paths,
      and a ``scenario_id`` linking it to an actual attempt.
    """

    verdict: Literal["pass", "partial", "fail"]
    scenario_results: list[ScenarioResult]
    findings: list[Finding] = []
    observations: list[str] = []


# ── Helpers ────────────────────────────────────────────────────────

def hash_spec(goal: str | None, spec: str | None) -> str:
    """Deterministic hash of goal+spec for scenario reuse tracking."""
    h = hashlib.sha256()
    h.update((goal or "").encode())
    h.update((spec or "").encode())
    return h.hexdigest()[:16]


def spec_hash_from_run(run: dict[str, Any]) -> str:
    """Extract and hash goal+spec from a run dict that may contain them."""
    return hash_spec(run.get("goal"), run.get("spec"))


def scenarios_to_json(scenarios: list[Scenario]) -> str:
    """Serialize scenarios to JSON for writing to ``l4_scratch/scenarios.json``."""
    return json.dumps(
        [s.model_dump() for s in scenarios],
        indent=2,
    )


# ── Structural validation ─────────────────────────────────────────-

MAX_ADHOC = 2


def report_consistent(report: L4Report, seeded: list[Scenario]) -> str | None:
    """Run six deterministic consistency checks on a report.

    Returns ``None`` if all pass, or an error message describing the
    first failure.  Any failure = structural failure — the report is
    self-contradictory and cannot be published.
    """
    seeded_ids = {s.id for s in seeded}
    result_ids = {x.scenario_id for x in report.scenario_results}

    # 1. Every seeded scenario must have a result
    if not seeded_ids <= result_ids:
        missing = seeded_ids - result_ids
        return f"seeded scenario(s) missing a result: {sorted(missing)}"

    # 2. Adhoc scenario count cap
    adhoc = [x for x in report.scenario_results if x.scenario_id not in seeded_ids]
    if len(adhoc) > MAX_ADHOC:
        return f"too many adhoc scenarios ({len(adhoc)} > {MAX_ADHOC})"

    # 3. Every finding must reference a known scenario_id
    for f in report.findings:
        if f.scenario_id not in result_ids:
            return f"finding references unknown scenario '{f.scenario_id}'"

    # 4. A failed/blocked scenario must have at least one finding
    for x in report.scenario_results:
        if x.outcome in ("fail", "blocked"):
            if not any(f.scenario_id == x.scenario_id for f in report.findings):
                return f"scenario '{x.scenario_id}' {x.outcome} with no finding"

    # 5. verdict=pass must have empty findings
    if report.verdict == "pass" and report.findings:
        return "verdict=pass with findings"

    # 6. verdict=partial with high-severity findings, or negative verdict with no findings
    high = [f for f in report.findings if f.severity == "high"]
    if report.verdict == "partial" and high:
        return "verdict=partial with high-severity findings"
    if report.verdict in ("partial", "fail") and not report.findings:
        return f"verdict={report.verdict} with no findings"

    return None


def resolve_where_paths(report: L4Report, worktree: str) -> bool:
    """Check that all ``where`` paths in findings resolve in the worktree.

    Returns True if ALL paths resolve (file exists or directory exists).
    """
    wt = Path(worktree)
    for f in report.findings:
        for w in f.where:
            if not (wt / w).exists():
                return False
    return True
