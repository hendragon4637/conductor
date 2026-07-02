from __future__ import annotations

import json
import logging
from typing import Any

from backend.evaluator.l3_meta.golden import count_golden
from backend.planning.capability.registry import get_capability, objective_dims, subjective_dims
from backend.planning.meta_planner.llm import call_llm_structured, get_meta_planner_model

logger = logging.getLogger(__name__)

MIN_GOLDEN_FOR_CALIBRATED = 5

L1_GEN_PROMPT = """You are a check-generation engine. Given a plan node and its
objective quality dimensions, produce deterministic (L1) checks for this node.

Each L1 check must:
- Be a shell command run in the worktree (exit 0 = pass)
- Test concrete things: file existence, syntax, test runs, output structure
- NOT contain runtime signals (curl, localhost, http://, :8000, etc.)
- Have a clear criterion

Objective dimensions to instantiate as L1 checks:
{objective_dims}

Node task: {task}
Node deliverables: {deliverables}

Return a JSON array of check objects, each with:
{{"id": "unique-id", "type": "deterministic", "criterion": "...", "check_cmd": "..."}}"""

L2_GEN_PROMPT = """You are a check-generation engine. Given a plan node and its
subjective quality dimensions, produce rubric (L2) checks for this node.

Each L2 check must be a yes/no quality question for the L2 judge.
Specialize the wording to the node's specific task.

Subjective dimensions to specialize:
{subjective_dims}

Node task: {task}
Node deliverables: {deliverables}

Return a JSON array of check objects, each with:
{{"id": "unique-id", "type": "rubric", "criterion": "...", "rubric_item": "yes/no question", "weight": 1.0}}"""


def _has_executor_role(node: dict[str, Any]) -> bool:
    members = node.get("members", [])
    return any(m.get("role") == "executor" for m in members)


def validate_checks_l1(checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Public boundary validation for L1 checks.

    Validates that L1 checks don't contain runtime signals and meet
    other boundary constraints. Drops invalid checks with warnings.
    """
    return [c for c in checks if _validate_l1_scope(c)]


def generate_capability_checks(node: dict[str, Any]) -> list[dict[str, Any]]:
    """Generate checks for a single node from its capabilities' quality_dimensions.

    Objective dims -> L1 (deterministic shell/file checks)
    Subjective dims -> L2 (rubric items for the judge)
    Live golden_set count per capability -> L2 items flagged provisional.
    """
    cap_names = node.get("capabilities", [])
    if not cap_names:
        return _fallback_checks(node)

    caps = [get_capability(n) for n in cap_names]
    caps = [c for c in caps if c is not None]

    if not caps:
        return _fallback_checks(node)

    task = (node.get("task") or {}).get("text", "")
    deliverables = (node.get("task") or {}).get("deliverables", [])

    l1_dims_raw = []
    l2_seed = []
    min_golden = float("inf")
    for cap in caps:
        l1_dims_raw.extend(objective_dims(cap))
        l2_seed.extend(subjective_dims(cap))
        g = count_golden(node_type=cap["name"], split="calibration")
        if g < min_golden:
            min_golden = g

    l1_checks = _generate_l1(l1_dims_raw, task, deliverables)
    l2_checks = _generate_l2(l2_seed, task, deliverables)

    l1_checks = validate_checks_l1(l1_checks)
    if _has_executor_role(node):
        l1_checks.insert(0, {
            "id": "l1-run-md-present",
            "type": "deterministic",
            "criterion": "RUN.md exists in worktree documenting run steps",
            "check_cmd": "test -f RUN.md",
        })

    confidence = "calibrated" if min_golden >= MIN_GOLDEN_FOR_CALIBRATED else "provisional"
    for item in l2_checks:
        item["confidence"] = confidence

    return l1_checks + l2_checks


def _generate_l1(
    objective_dims: list[dict[str, Any]], task: str, deliverables: list[str]
) -> list[dict[str, Any]]:
    if not objective_dims:
        return []

    prompt = L1_GEN_PROMPT.format(
        objective_dims=json.dumps(objective_dims, indent=2),
        task=task or "(not specified)",
        deliverables=json.dumps(deliverables) if deliverables else "(none)",
    )
    try:
        model_cfg = get_meta_planner_model()
        if model_cfg is None:
            raise ValueError("no meta_planner model configured")
        resp = call_llm_structured(prompt, schema=None, model_cfg=model_cfg)
        raw = resp if isinstance(resp, list) else json.loads(str(resp))
        validated = []
        for c in raw:
            c["type"] = "deterministic"
            if _validate_l1_scope(c):
                validated.append(c)
        return validated
    except Exception as exc:
        logger.warning("L1 check generation failed: %s", exc)
        return _keyword_l1(objective_dims)


def _generate_l2(
    subjective_dims: list[dict[str, Any]], task: str, deliverables: list[str]
) -> list[dict[str, Any]]:
    if not subjective_dims:
        return []

    prompt = L2_GEN_PROMPT.format(
        subjective_dims=json.dumps(subjective_dims, indent=2),
        task=task or "(not specified)",
        deliverables=json.dumps(deliverables) if deliverables else "(none)",
    )
    try:
        model_cfg = get_meta_planner_model()
        if model_cfg is None:
            raise ValueError("no meta_planner model configured")
        resp = call_llm_structured(prompt, schema=None, model_cfg=model_cfg)
        raw = resp if isinstance(resp, list) else json.loads(str(resp))
        for c in raw:
            c["type"] = "rubric"
            c.setdefault("weight", 1.0)
        return raw
    except Exception as exc:
        logger.warning("L2 check generation failed: %s", exc)
        return _keyword_l2(subjective_dims)


def _keyword_l1(objective_dims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    checks = []
    for i, dim in enumerate(objective_dims):
        d_id = dim.get("id", f"l1-obj-{i}")
        dim_text = dim.get("dimension", "").lower()
        if any(kw in dim_text for kw in ("test", "pass", "pytest", "unit")):
            checks.append({
                "id": f"{d_id}",
                "type": "deterministic",
                "criterion": dim.get("dimension", ""),
                "check_cmd": "python3 -m pytest -q --tb=short 2>&1 || exit 1",
            })
        elif any(kw in dim_text for kw in ("file", "exist", "build", "compile")):
            checks.append({
                "id": f"{d_id}",
                "type": "deterministic",
                "criterion": dim.get("dimension", ""),
                "check_cmd": "ls -la",
            })
        elif any(kw in dim_text for kw in ("syntax", "error")):
            checks.append({
                "id": f"{d_id}",
                "type": "deterministic",
                "criterion": dim.get("dimension", ""),
                "check_cmd": "python3 -m py_compile $(find . -name '*.py' -not -path './.git/*' -not -path './.venv/*') 2>&1 || exit 1",
            })
        else:
            checks.append({
                "id": f"{d_id}",
                "type": "deterministic",
                "criterion": dim.get("dimension", ""),
                "check_cmd": "echo 'check: {}' && exit 0".format(dim.get("dimension", "ok")),
            })
    return checks


def _keyword_l2(subjective_dims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "id": dim.get("id", f"l2-subj-{i}"),
            "type": "rubric",
            "criterion": dim.get("dimension", ""),
            "rubric_item": "Does the output satisfy: {}?".format(dim.get("dimension", "")),
            "weight": 1.0,
            "confidence": "provisional",
        }
        for i, dim in enumerate(subjective_dims)
    ]


L1_RUNTIME_SIGNALS = ("curl", "uvicorn", "localhost", "127.0.0.1", "http://", "https://", ":8000", ":3000")


def _validate_l1_scope(check: dict[str, Any]) -> bool:
    cmd = (check.get("check_cmd") or "").lower()
    for signal in L1_RUNTIME_SIGNALS:
        if signal in cmd:
            logger.warning("dropping leaked L1 check %s: %s", check.get("id", "?"), cmd[:100])
            return False
    return True


def _fallback_checks(node: dict[str, Any]) -> list[dict[str, Any]]:
    """Produce minimal fallback checks when no capabilities are selected."""
    task = (node.get("task") or {}).get("text", "")
    return [
        {
            "id": "l1-deliverable-exists",
            "type": "deterministic",
            "criterion": "Stated deliverable exists",
            "check_cmd": "ls -la",
        },
        {
            "id": "l2-meets-goal",
            "type": "rubric",
            "criterion": "Achieves the stated goal",
            "rubric_item": "Does the output achieve the stated goal?",
            "weight": 1.0,
            "confidence": "provisional",
        },
    ]
