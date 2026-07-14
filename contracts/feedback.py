"""Structured feedback contract with deterministic anti-filler validation.

Every L2 judge dimension and plan-edit revision produces a structured
``{what, where, why, how}`` block.  This module validates CONTENT, not
just shape — filler ("Address the rubric item") is caught deterministically
via pydantic before it reaches a remediation brief.

Two-tier enforcement (shared philosophy with the plan assembler):
  1. Pydantic (this file) — structural + anti-boilerplate, deterministic.
  2. L2 judge (runtime) — content quality, LLM-based.
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, Field, field_validator

# ── Feedback parsing ──────────────────────────────────────────────────────────


def parse_feedback(reason: Any) -> dict:
    """Extract structured feedback dict from GEval ``reason``.

    Expects strict JSON ``{"what","where","why","how"}`` per the feedback
    contract.  Falls back to regex extraction, then labels the result
    ``{_unstructured: true}`` for monitoring.
    """
    if reason is None:
        return {"what": "", "where": "unspecified", "why": "", "how": "unspecified", "_unstructured": True}
    if isinstance(reason, dict):
        return reason
    raw = str(reason)
    try:
        return json.loads(raw)
    except Exception:
        pass
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except Exception:
            pass
    return {
        "what": raw.strip()[:500],
        "where": "unspecified",
        "why": "judge returned unstructured prose",
        "how": "unspecified",
        "_unstructured": True,
    }


# ── Phrases that indicate boilerplate / filler feedback ─────────────────────

BANNED_PHRASES: list[str] = [
    "address the rubric",
    "fix the issue",
    "implement the requirement",
    "as described",
    "per the criterion",
    "see above",
    "n/a",
    "unspecified",
]

# Regex matching concrete file paths or package paths
PATHY = re.compile(r"[\w./-]+\.(?:py|js|ts|md|toml|json|yaml|yml|txt|html|css|sql)|[\w./-]+/")

# Concrete action verbs expected in the ``how`` field
ACTION_VERBS = [
    "create", "add", "change", "rename", "move",
    "implement", "declare", "define", "install",
    "write", "fix ",  # trailing space to avoid "fixed" false positive
]


class DimFeedback(BaseModel):
    """Validated structured feedback for a single evaluation dimension.

    All four fields are required and content-validated.  ``_degraded``
    is set by the validation layer when the LLM's feedback fails content
    checks even after one bounded re-ask.
    """

    what: str
    where: str
    why: str
    how: str
    _degraded: bool = False

    @field_validator("what", "why", "how")
    @classmethod
    def not_filler(cls, v: str, info: Any) -> str:
        lv = v.lower().strip()
        assert len(lv) >= 15, f"{info.field_name} too short ({len(lv)} chars, min 15)"
        for banned in BANNED_PHRASES:
            assert banned not in lv, (
                f"{info.field_name} is boilerplate (contains {banned!r})"
            )
        return v

    @field_validator("where")
    @classmethod
    def looks_like_path(cls, v: str) -> str:
        assert PATHY.search(v), (
            f"where must reference a concrete file/path, got {v!r}"
        )
        return v

    @field_validator("how")
    @classmethod
    def concrete_action(cls, v: str) -> str:
        lv = v.lower()
        assert any(verb in lv for verb in ACTION_VERBS), (
            f"how must state a concrete action (one of: {ACTION_VERBS}), got {v!r}"
        )
        return v


class AcceptanceCriterion(BaseModel):
    """A single acceptance criterion for a plan node — measurable, path-anchored, verifiable.

    Written by the planning harness, rendered verbatim in the agent brief,
    and used as the source of L2 judge evaluation steps. Same anti-filler
    rules as ``DimFeedback``.
    """
    id: str
    what: str = Field(description="Measurable statement, e.g. 'pyproject.toml declares fastapi, uvicorn, pytest'")
    where: list[str] = Field(description="Expected file paths the criterion applies to")
    how_verified: str = Field(description="What evidence satisfies it, e.g. 'file exists AND [project.dependencies] lists all three'")
    why: str | None = Field(default=None, description="Optional rationale")

    @field_validator("what")
    @classmethod
    def measurable(cls, v: str) -> str:
        lv = v.lower().strip()
        assert len(lv) >= 15, f"what too short ({len(lv)} chars, min 15)"
        for banned in BANNED_PHRASES:
            assert banned not in lv, f"what is boilerplate (contains {banned!r})"
        return v

    @field_validator("where")
    @classmethod
    def paths_are_concrete(cls, v: list[str]) -> list[str]:
        assert v, "where must have at least one path"
        for p in v:
            assert PATHY.search(p), f"where path {p!r} does not look like a concrete path"
        return v


def validate_feedback(fb: dict) -> tuple[DimFeedback | None, list[str]]:
    """Validate *fb* dict against DimFeedback.

    Returns ``(model, errors)`` where:
      - ``model`` is the validated ``DimFeedback`` (or ``None`` on failure).
      - ``errors`` is a list of human-readable field errors (empty on success).
    """
    errors: list[str] = []
    try:
        validated = DimFeedback(**fb)
        return validated, []
    except Exception as exc:
        errors.append(str(exc))
        return None, errors


def try_validate_feedback(
    fb: dict,
    previous_errors: list[str] | None = None,
) -> tuple[dict, bool]:
    """Try to validate *fb*, marking degraded if *previous_errors* given.

    Args:
        fb: Raw feedback dict (from L2 judge parse).
        previous_errors: If provided, this is a re-ask attempt — failure
            means degraded rather than retrying again.

    Returns:
        ``(fb, degraded)`` — ``fb`` is the input dict plus a ``_degraded``
        key if validation failed; ``degraded`` is True/False.
    """
    validated, errors = validate_feedback(fb)
    if validated is not None:
        return validated.model_dump(), False

    if previous_errors:
        # Already tried one re-ask — mark degraded
        fb["_degraded"] = True
        return fb, True

    # First failure — caller should re-ask; no marker yet
    return fb, False


REASK_PROMPT = """[FEEDBACK RE-ASK — format correction only]
Your previous evaluation included structured feedback that failed validation.

Original rubric: {dim}
Feedback given: {previous}
Validation errors: {errors}

Re-emit ONLY a strict JSON object with these four fields:
  "what": which specific requirement failed or passed (concrete, ≥15 chars)
  "where": file:function or exact path in the artifact (must reference a real path)
  "why": root cause in one sentence (concrete, ≥15 chars)
  "how": the concrete change that would satisfy this criterion (must contain an action verb like create/add/change/implement)

NO preamble, NO commentary outside the JSON. The "where" must reference a concrete file path. The "how" must state a concrete action."""


def get_dim_feedback(
    metric: Any,
    dim: str,
    test_case: Any,
    raw_reason: str = "",
) -> tuple[dict, bool]:
    """Get validated DimFeedback from a GEval metric with bounded 1 re-ask.

    Parses the GEval ``reason``, validates against ``DimFeedback``, and if
    validation fails issues exactly one targeted re-ask via GEval with an
    augmented test case.  Persistent failure is flagged ``_degraded``.

    Args:
        metric: An already-measured GEval metric (``metric.reason`` set).
        dim: The rubric dimension ID (for error context).
        test_case: The ``LLMTestCase`` used for the original measurement.
        raw_reason: Pre-captured reason string (use this instead of
            reading ``metric.reason``, so the caller can preserve it).

    Returns:
        ``(feedback_dict, degraded)`` where ``feedback_dict`` has
        ``{what, where, why, how, [_degraded]}`` keys and ``degraded``
        is True if the re-ask also failed.
    """
    from deepeval.test_case import LLMTestCase

    fb = parse_feedback(raw_reason or metric.reason)
    validated, errors = validate_feedback(fb)

    if validated is not None:
        return validated.model_dump(), False

    # ── One bounded re-ask via GEval (augmented input, same metric) ──
    reask_input = REASK_PROMPT.format(
        dim=dim,
        previous=raw_reason[:2000] if raw_reason else str(metric.reason)[:2000],
        errors="; ".join(errors),
    )
    tc2 = LLMTestCase(
        input=reask_input,
        actual_output=test_case.actual_output,
    )
    metric.measure(tc2)

    fb2 = parse_feedback(metric.reason)
    validated2, _ = validate_feedback(fb2)

    if validated2 is not None:
        fb2["_reask_used"] = True
        return validated2.model_dump(), False

    fb2["_degraded"] = True
    return fb2, True
