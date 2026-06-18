"""L4 persona/usage simulation — catch UX friction by using the product as a user.

An agent (currently a scripted behavior runner, extensible to LLM-driven)
launches the finished product and performs persona-defined actions,
recording friction observations per dimension.
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PERSONA_DIR = Path(__file__).resolve().parent / "personas"


# ── Data types ───────────────────────────────────────────────────────────────


@dataclass
class StepObservation:
    """Result of executing a single persona action step."""
    action: str
    method: str
    path: str
    status_code: int | None = None
    response_body: str = ""
    duration_ms: float = 0.0
    error: str | None = None
    expectations_met: bool = False


@dataclass
class BehaviorResult:
    """Result of executing all steps in a persona behavior."""
    behavior_id: str
    description: str
    steps: list[StepObservation] = field(default_factory=list)
    success: bool = False
    friction_score: float = 1.0
    """0.0 = smooth, 1.0 = completely broken."""


@dataclass
class L4Report:
    """Structured output of a persona simulation run.

    Args:
        persona_name: Name of the persona used.
        goal: The persona's stated goal.
        behaviors: Results per behavior.
        dimensions: Score per dimension (0.0 = no friction, 1.0 = max friction).
        overall_friction: Aggregated friction score across all dimensions.
        summary: Optional natural-language summary of findings.
    """
    persona_name: str
    goal: str
    behaviors: list[BehaviorResult] = field(default_factory=list)
    dimensions: dict[str, float] = field(default_factory=dict)
    overall_friction: float = 0.0
    summary: str = ""


# ── Persona loading ──────────────────────────────────────────────────────────


def load_persona(name: str = "casual_user") -> dict[str, Any]:
    """Load a persona definition from a YAML file in the personas directory.

    Args:
        name: Persona name (without ``.yaml`` extension).

    Returns:
        Parsed persona dict with keys: ``name``, ``goal``, ``behaviors``,
        ``report_dimensions``.

    Raises:
        FileNotFoundError: If the persona file does not exist.
    """
    import yaml

    path = PERSONA_DIR / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Persona '{name}' not found at {path}")
    with open(path) as f:
        return yaml.safe_load(f)


# ── Step execution ───────────────────────────────────────────────────────────


def _resolve_refs(text: str, refs: dict[str, Any]) -> str:
    """Replace ``{ref_id}`` placeholders with values from the refs dict."""
    for key, val in refs.items():
        text = text.replace(f"{{{key}}}", str(val))
    return text


def _execute_http_step(
    step: dict[str, Any],
    base_url: str,
    refs: dict[str, Any],
) -> StepObservation:
    """Execute a single HTTP request step and return the observation."""
    method = step.get("method", "GET").upper()
    raw_path = step.get("path", "/")
    resolved_path = _resolve_refs(raw_path, refs)
    url = f"{base_url.rstrip('/')}{resolved_path}"
    body = step.get("json")
    headers = {"Content-Type": "application/json"}

    obs = StepObservation(
        action=step.get("action", "request"),
        method=method,
        path=resolved_path,
    )

    data = json.dumps(body).encode() if body is not None else None
    start = time.monotonic()
    try:
        req = urllib.request.Request(
            url, data=data, headers=headers, method=method,
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            obs.status_code = resp.status
            obs.response_body = resp.read().decode(errors="replace")[:2000]
    except urllib.error.HTTPError as e:
        obs.status_code = e.code
        try:
            obs.response_body = e.read().decode(errors="replace")[:2000]
        except Exception:
            obs.response_body = ""
    except Exception as e:
        obs.error = str(e)
        obs.status_code = None
    obs.duration_ms = (time.monotonic() - start) * 1000

    return obs


def _check_expectations(
    step: dict[str, Any],
    step_obs: StepObservation,
) -> bool:
    """Check whether the step's expectations were met."""
    expect = step.get("expect", {})
    status_in = expect.get("status_in")
    if status_in and step_obs.status_code is not None:
        if step_obs.status_code not in status_in:
            return False

    body_contains = expect.get("body_contains")
    if body_contains and body_contains not in step_obs.response_body:
        return False

    body_is_array = expect.get("body_is_array")
    if body_is_array and step_obs.response_body:
        try:
            parsed = json.loads(step_obs.response_body)
            if not isinstance(parsed, list):
                return False
        except (json.JSONDecodeError, TypeError):
            return False

    body_not_empty = expect.get("body_not_empty")
    if body_not_empty and not step_obs.response_body.strip():
        return False

    return True


def _execute_behavior(
    behavior: dict[str, Any],
    base_url: str,
    refs: dict[str, Any],
) -> BehaviorResult:
    """Execute all steps of a single behavior and return the result."""
    result = BehaviorResult(
        behavior_id=behavior.get("id", ""),
        description=behavior.get("description", ""),
    )

    steps = behavior.get("steps", [])
    all_met = True

    for step in steps:
        action = step.get("action", "request")

        if action == "request":
            obs = _execute_http_step(step, base_url, refs)
            obs.expectations_met = _check_expectations(step, obs)
            if not obs.expectations_met:
                all_met = False
            result.steps.append(obs)
        elif action == "extract":
            # Extract a value from a previous response into refs
            ref_key = step.get("ref_key", "")
            json_path = step.get("json_path", "")
            if json_path and result.steps:
                prev = result.steps[-1]
                try:
                    body = json.loads(prev.response_body)
                    val = body
                    for part in json_path.split("."):
                        if isinstance(val, dict):
                            val = val.get(part, "")
                        elif isinstance(val, list) and part.isdigit():
                            val = val[int(part)]
                        else:
                            val = ""
                            break
                    refs[ref_key] = val
                except (json.JSONDecodeError, TypeError, IndexError):
                    pass
            obs = StepObservation(action="extract", method="N/A", path="")
            obs.expectations_met = True
            result.steps.append(obs)

    result.success = all_met
    # Friction score: 0 if all expectations met, scales with failures
    failed = sum(1 for s in result.steps if not s.expectations_met)
    total = len(result.steps)
    severity = behavior.get("friction_severity", "high")
    severity_weight = {"low": 0.3, "medium": 0.6, "high": 1.0}.get(severity, 1.0)
    result.friction_score = round((failed / total if total > 0 else 0) * severity_weight, 4)

    return result


# ── Scoring ──────────────────────────────────────────────────────────────────


def _score_discoverability(results: list[BehaviorResult]) -> float:
    """Score discoverability — can the user figure out the API without docs?

    - Successful first-attempt behaviors suggest discoverable design.
    - Failed behaviors suggest poor discoverability.
    Returns 0.0 (perfect) to 1.0 (undiscoverable).
    """
    if not results:
        return 0.0
    failures = sum(1 for r in results if not r.success)
    return round(failures / len(results), 4)


def _score_error_feedback(results: list[BehaviorResult]) -> float:
    """Score error feedback quality.

    - Behaviors that intentionally test errors (invalid input, 404s)
      should return clear status codes and non-empty bodies.
    - Empty error responses or wrong status codes = poor feedback.
    Returns 0.0 (perfect) to 1.0 (no useful feedback).
    """
    error_related = [
        r for r in results
        if any(kw in r.behavior_id for kw in ("invalid", "nonexistent", "deleted"))
    ]
    if not error_related:
        return 0.0

    poor = 0
    for r in error_related:
        for s in r.steps:
            if s.expectations_met:
                continue
            if s.error:
                poor += 1  # connection error = terrible feedback
            elif s.status_code and s.response_body.strip():
                pass  # got an error with a body = acceptable
            else:
                poor += 1  # empty error response

    return round(poor / max(len(error_related), 1), 4)


def _score_friction(results: list[BehaviorResult]) -> float:
    """Score overall friction — aggregated from behavior friction scores.

    Returns 0.0 (smooth) to 1.0 (unusable).
    """
    if not results:
        return 0.0
    avg = sum(r.friction_score for r in results) / len(results)
    return round(avg, 4)


# ── Langfuse logging ─────────────────────────────────────────────────────────


def _log_to_langfuse(report: L4Report) -> None:
    """Write L4 usage simulation metrics to Langfuse."""
    try:
        from backend.observability.langfuse_client import get_langfuse
        lf = get_langfuse()
        trace = lf.trace(
            name=f"l4-persona-{report.persona_name}",
            metadata={
                "persona": report.persona_name,
                "goal": report.goal,
                "source": "l4_persona",
                "dimensions": report.dimensions,
            },
        )
        trace.score(
            name="l4_usage",
            value=1.0 - report.overall_friction,
            data_type="NUMERIC",
            comment=(
                f"overall_friction={report.overall_friction:.2f}, "
                f"discoverability={report.dimensions.get('discoverability', 0):.2f}, "
                f"error_feedback={report.dimensions.get('error_feedback', 0):.2f}"
            ),
        )
        lf.flush()
    except Exception:
        pass  # Langfuse write is best-effort


# ── Main entry point ─────────────────────────────────────────────────────────


def run_l4(
    persona_name: str = "casual_user",
    base_url: str = "http://127.0.0.1:8000",
) -> L4Report:
    """Run an L4 persona usage simulation against a running product.

    Loads the persona definition, executes its behaviors against the
    product at ``base_url``, scores friction per dimension, and logs
    results to Langfuse.

    Args:
        persona_name: Name of the persona to simulate.
        base_url: Base URL of the running product instance.

    Returns:
        An ``L4Report`` with per-behavior results, dimension scores,
        and an overall friction metric.

    Raises:
        FileNotFoundError: If the persona YAML does not exist.
        ConnectionError: If the product is unreachable on the first behavior.
    """
    persona = load_persona(persona_name)
    refs: dict[str, Any] = {}
    behavior_results: list[BehaviorResult] = []

    for behavior in persona.get("behaviors", []):
        # Check dependencies
        depends = behavior.get("depends_on")
        if depends:
            dep_result = next(
                (r for r in behavior_results if r.behavior_id == depends),
                None,
            )
            if dep_result and not dep_result.success:
                logger.info(
                    "Skipping %s — dependency %s failed",
                    behavior.get("id"), depends,
                )
                continue

        result = _execute_behavior(behavior, base_url, refs)
        behavior_results.append(result)

    # Raise on first behavior failure due to connection issues
    if behavior_results and behavior_results[0].steps:
        first = behavior_results[0].steps[0]
        if first.error and "Connection" in (first.error or ""):
            raise ConnectionError(
                f"Product unreachable at {base_url}: {first.error}"
            )

    # Score dimensions
    dimensions = {}
    for dim in persona.get("report_dimensions", []):
        if dim == "discoverability":
            dimensions[dim] = _score_discoverability(behavior_results)
        elif dim == "error_feedback":
            dimensions[dim] = _score_error_feedback(behavior_results)
        elif dim == "friction":
            dimensions[dim] = _score_friction(behavior_results)

    overall = round(
        sum(dimensions.values()) / len(dimensions) if dimensions else 0.0,
        4,
    )

    report = L4Report(
        persona_name=persona.get("name", persona_name),
        goal=persona.get("goal", ""),
        behaviors=behavior_results,
        dimensions=dimensions,
        overall_friction=overall,
    )

    _log_to_langfuse(report)
    logger.info(
        "L4 %s: friction=%.4f dims=%s",
        persona_name, overall, dimensions,
    )

    return report
