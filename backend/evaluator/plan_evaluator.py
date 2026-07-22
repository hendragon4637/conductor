from __future__ import annotations

"""Plan-evaluator: gates DAG structure BEFORE execution (File 04A).

L1 structural asserts (acyclic, nodes complete, deps resolve).
L2 plan rubric (covers_goal, right_sized, deps_correct, measurable)
via meta_planner LLM call, NOT the node-level L2 judge.

File 03 adds a capability-aware staffing gate: the evaluator checks
whether each node's assigned agent_config can actually do the work.

Output ``plan_goal_review`` (0-1) stored on the run.
"""

import json
import logging
import os
from dataclasses import dataclass, field

import psycopg
from backend.evaluator.rubrics import load_rubric
from backend.planning.meta_planner.llm import call_llm_structured, get_meta_planner_model
from pydantic import BaseModel, Field
from contracts.feedback import validate_feedback

logger = logging.getLogger(__name__)

L1_STRUCTURAL_RUBRIC = "plan_structure"

# ── Capability-aware staffing gate (File 03/04) ─────────────────────

# Domain→role→capability mapping (retained as fallback for legacy agent_configs)
_CAPABILITY_MAP: dict[str, dict[str, list[str]]] = {
    "backend": {
        "executor": ["backend", "tests"],
        "planner": ["planning", "analysis"],
        "reviewer": ["review", "code_review"],
    },
    "finance": {
        "executor": ["fullstack", "backend", "frontend", "tests"],
        "planner": ["planning", "finance", "backend"],
        "reviewer": ["review", "finance", "backend", "tests"],
    },
    "fullstack": {
        "executor": ["fullstack", "backend", "frontend", "tests"],
    },
    "general": {
        "executor": ["backend", "tests", "general"],
        "planner": ["planning", "general"],
        "reviewer": ["review", "general"],
    },
}


def _get_db_url() -> str:
    return os.environ["DATABASE_URL"]


# In-memory fallback for agent config capabilities (mirrors scripts/seed_agent_configs.py)
_FALLBACK_AC_CAPS: dict[str, list[str]] = {
    "software-fullstack-executor": ["frontend", "backend_api", "cli_tool", "generic"],
    "backend-api-executor": ["backend_api", "cli_tool", "generic"],
    "data-executor": ["data_pipeline", "analytics_assistant", "generic"],
    "research-writer": ["research_report", "generic"],
    "code-reviewer": ["backend_api", "frontend", "cli_tool", "research_report", "generic"],
    "l4-persona": [],
}

_FALLBACK_AC_TOOLS: dict[str, list[str]] = {
    "software-fullstack-executor": ["write_file", "shell", "browser"],
    "backend-api-executor": ["write_file", "shell"],
    "data-executor": ["write_file", "shell", "read_data"],
    "research-writer": ["read_web", "write_file"],
    "code-reviewer": ["read_file", "shell", "browser"],
    "l4-persona": ["browser", "shell", "http", "read_file"],
}


def _get_agent_config_capabilities(agent_config_id: str) -> list[str]:
    try:
        dsn = _get_db_url()
        with psycopg.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT new_capabilities, domain, role FROM agent_configs WHERE agent_config_id = %s AND active = TRUE",
                (agent_config_id,),
            )
            row = cur.fetchone()
            if row:
                new_caps, domain, role = row[0], row[1], row[2]
                if new_caps and isinstance(new_caps, (list, str)):
                    if isinstance(new_caps, str):
                        import json
                        new_caps = json.loads(new_caps)
                    if new_caps:
                        return new_caps
                return _CAPABILITY_MAP.get(domain, {}).get(role, _CAPABILITY_MAP.get("general", {}).get(role, ["general"]))
    except Exception:
        logger.debug("Failed to query agent_config '%s' for capabilities", agent_config_id)
    return _FALLBACK_AC_CAPS.get(agent_config_id, ["general"])


def _get_agent_config_tools(agent_config_id: str) -> list[str]:
    try:
        dsn = _get_db_url()
        with psycopg.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT tools FROM agent_configs WHERE agent_config_id = %s AND active = TRUE",
                (agent_config_id,),
            )
            row = cur.fetchone()
            if row:
                tools = row[0]
                if isinstance(tools, str):
                    import json
                    tools = json.loads(tools)
                if isinstance(tools, list):
                    return tools
    except Exception:
        logger.debug("Failed to query agent_config '%s' for tools", agent_config_id)
    return _FALLBACK_AC_TOOLS.get(agent_config_id, [])


def staffing_check(node: dict) -> list[str]:
    """Two set-checks for a single node: realizability + coverage.

    1. Realizability: every capability's required_tools ⊆ backend.tools
    2. Coverage: node.capabilities ⊆ agent_config.new_capabilities

    Returns list of failure strings (empty = node passes staffing).
    """
    from backend.planning.capability.harness_profiles import HARNESS_PROFILES
    from backend.planning.capability.registry import get_capability

    fails: list[str] = []
    cap_names = node.get("capabilities", [])
    if not cap_names:
        return fails

    backend = "opencode"
    agent_config_id = "opencode:backend-executor"
    if node.get("members"):
        backend = node["members"][0].get("backend", backend)
        agent_config_id = node["members"][0].get("agent_config", agent_config_id)

    # Fetch capability definitions to check required_tools
    caps = [get_capability(n) for n in cap_names]
    caps = [c for c in caps if c is not None]

    # (1) Realizability: capability required_tools ⊆ backend tools
    backend_tools = set(HARNESS_PROFILES.get(backend, {}).get("tools", []))
    for cap in caps:
        required = set(cap.get("required_tools", []))
        if not required:
            continue
        missing = required - backend_tools
        if missing:
            fails.append(
                f"{node.get('id', '?')}: {cap['name']} needs tools {missing} "
                f"but backend '{backend}' does not provide them"
            )

    # (2) Coverage: node capabilities ⊆ agent_config capabilities
    ac_caps = set(_get_agent_config_capabilities(agent_config_id))
    node_caps = set(cap_names)
    uncovered = node_caps - ac_caps
    if uncovered:
        fails.append(
            f"{node.get('id', '?')}: node requires capabilities {uncovered} "
            f"but '{agent_config_id}' does not declare them"
        )

    return fails


def staffing_l1(dag: list[dict]) -> list[str]:
    """Run staffing set-checks across all nodes in the DAG.

    For each node, checks (1) tool realizability and (2) capability coverage.
    Uses the new capability model when available, falls back to keyword-based
    inference for legacy nodes.
    """
    all_fails: list[str] = []
    for n in dag:
        all_fails.extend(staffing_check(n))
    return all_fails


# ── plan_l2 response schema ─────────────────────────────────────────

class JudgeItemResponse(BaseModel):
    """A single rubric item judged by the plan-level L2."""
    id: str = Field(description="Rubric item ID (covers_goal, right_sized, deps_correct, measurable)")
    met: bool = Field(description="True if this rubric criterion is satisfied by the plan")
    what: str | None = Field(default=None, description="Which specific criterion aspect failed")
    where: str | None = Field(default=None, description="Which node(s) or field(s) are problematic")
    why: str | None = Field(default=None, description="Root cause in one sentence")
    how: str | None = Field(default=None, description="Concrete action to fix the issue")

class PlanJudgeResponse(BaseModel):
    """All rubric items judged by the plan-level L2."""
    items: list[JudgeItemResponse] = Field(description="Judgments for each rubric item")

# ── Plan L2 prompt template ─────────────────────────────────────────

PLAN_JUDGE_PROMPT = """You are evaluating a plan DAG before execution. Rate each rubric item as met or not met.

SEQUENTIAL CONSTRAINT — Nodes MUST be sequential (each depends on the previous).
Do NOT flag sequential dependencies as unnecessary. Sequential execution is required
by design, even if it costs efficiency. Parallel DAGs are NOT allowed.

Plan goal:
{plan_goal}

Plan nodes:
{plan_nodes}

Staffing (assigned agent_config per node with capabilities):
{staffing}

Rubric:
{rubric}

For each rubric item, determine whether the plan satisfies it. Return the judgments as a JSON object with an "items" array, each with:
  - "id": rubric item ID
  - "met": true/false
  - "what" (optional): which specific aspect failed
  - "where" (optional): which node(s) or field(s) are problematic  
  - "why" (optional): root cause in one sentence
  - "how" (optional): concrete action to fix"""

# ── Data classes ────────────────────────────────────────────────────


@dataclass
class PlanL1Result:
    """Result of structural validation on a plan DAG."""
    passed: bool
    checks: list[dict] = field(default_factory=list)
    note: str = ""


@dataclass
class PlanL2Result:
    """Result of plan-level L2 rubric evaluation (not the node-level L2)."""
    score: float = 0.0
    judgments: list[dict] = field(default_factory=list)
    hard_failures: list[dict] = field(default_factory=list)
    raw_response: str | None = None


@dataclass
class PlanEvalResult:
    """Combined result of plan evaluation (L1 + L2)."""
    l1: PlanL1Result
    l2: PlanL2Result | None = None
    plan_goal_review: float = 0.0
    passed: bool = False
    hard_failures: list[dict] = field(default_factory=list)


def _check_acyclic(dag: list[dict]) -> bool:
    """Check that the DAG has no cycles (simple DFS-based cycle detection).

    Args:
        dag: List of node dicts, each with ``id`` and ``depends_on``.

    Returns:
        True if acyclic, False if a cycle is detected.
    """
    node_ids = {n["id"] for n in dag}
    dep_map: dict[str, list[str]] = {}
    for n in dag:
        dep_map[n["id"]] = [d for d in n.get("depends_on", []) if d in node_ids]

    visited: set[str] = set()
    in_stack: set[str] = set()

    def dfs(nid: str) -> bool:
        visited.add(nid)
        in_stack.add(nid)
        for dep in dep_map.get(nid, []):
            if dep not in visited:
                if dfs(dep):
                    return True
            elif dep in in_stack:
                return True
        in_stack.discard(nid)
        return False

    for nid in node_ids:
        if nid not in visited:
            if dfs(nid):
                return False
    return True


def _validate_l1_check_ids(dag: list[dict]) -> list[dict]:
    """Validate that every L1 (deterministic) check references a known preset.

    Loads the valid L1 check ID set from ``check_generator`` (canonical pool
    + agent_config default_checks).  Any L1 check whose ``id`` is not in the
    set is a hallucination — the LLM invented a check command that doesn't
    exist in any preset.

    Returns:
        List of hallucination dicts with ``node_id``, ``check_id``,
        ``check_cmd`` (truncated).  Empty list = all L1 checks are valid.
    """
    from backend.planning.meta_planner.check_generator import get_valid_l1_ids
    valid_ids = get_valid_l1_ids()
    try:
        from backend.planning.capability.registry import all_capabilities
        for cap in all_capabilities():
            for dim in cap.get("quality_dimensions") or []:
                if dim.get("id"):
                    valid_ids.add(dim["id"])
    except Exception:
        pass
    hallucinations: list[dict] = []
    for n in dag:
        nid = n.get("id", "?")
        for c in (n.get("checks") or []):
            if c.get("type") == "deterministic":
                cid = c.get("id", "")
                if cid not in valid_ids:
                    hallucinations.append({
                        "node_id": nid,
                        "check_id": cid,
                        "check_cmd": (c.get("check_cmd") or "")[:120],
                    })
    return hallucinations


def run_plan_l1(dag: list[dict]) -> PlanL1Result:
    """Run L1 structural checks on the plan DAG.

    Checks:
    1. At least one node exists.
    2. All nodes have required fields (id, members, task.text, success.text).
    3. Dependencies reference existing node IDs.
    4. DAG is acyclic.
    5. Every node has at least one check.
    6. No hallucinated L1 checks (every L1 check id is in the known preset pool).

    Args:
        dag: List of node dicts from the plan.

    Returns:
        ``PlanL1Result`` with pass/fail and per-check detail.
    """
    checks: list[dict] = []
    node_ids = {n.get("id", "") for n in dag}

    # Check 1: at least one node
    if len(dag) < 1:
        checks.append({"check": "at_least_one_node", "passed": False, "detail": "DAG has no nodes"})
        return PlanL1Result(passed=False, checks=checks, note="Empty DAG")

    checks.append({"check": "at_least_one_node", "passed": True, "detail": f"{len(dag)} nodes"})

    all_ok = True
    for i, n in enumerate(dag):
        nid = n.get("id", f"node-{i}")
        missing: list[str] = []

        # Validate node ID naming convention: must follow "node-NNN" pattern
        import re as _re
        if not _re.match(r"^node-\d+$", nid):
            all_ok = False
            checks.append({
                "check": f"node_{nid}_naming",
                "passed": False,
                "detail": (
                    f"Node ID '{nid}' does not follow the required naming convention. "
                    "Node IDs MUST be in the format 'node-NNN' with zero-padded numbers "
                    "(e.g. 'node-001', 'node-002', 'node-003'). "
                    f"Rename '{nid}' to a numbered identifier like 'node-00X'."
                ),
            })

        if not n.get("members"):
            missing.append("members")
        if not n.get("task") or not n.get("task", {}).get("text"):
            missing.append("task.text")
        if not n.get("success") or not n.get("success", {}).get("text"):
            missing.append("success.text")
        node_checks = n.get("checks", [])
        if not node_checks:
            missing.append("checks (empty)")

        if missing:
            all_ok = False
            checks.append({
                "check": f"node_{nid}_fields",
                "passed": False,
                "detail": f"Missing: {', '.join(missing)}",
            })
        else:
            checks.append({
                "check": f"node_{nid}_fields",
                "passed": True,
                "detail": f"{len(node_checks)} checks",
            })

        # Check dependency resolution
        for dep in n.get("depends_on", []):
            if dep not in node_ids:
                all_ok = False
                checks.append({
                    "check": f"node_{nid}_dep_{dep}",
                    "passed": False,
                    "detail": f"Dependency '{dep}' not found in DAG",
                })

    # Check acyclic
    if not _check_acyclic(dag):
        all_ok = False
        checks.append({"check": "acyclic", "passed": False, "detail": "Cycle detected in DAG"})
    else:
        checks.append({"check": "acyclic", "passed": True, "detail": "No cycles"})

    # Check 6: no hallucinated L1 checks
    hallucinations = _validate_l1_check_ids(dag)
    if hallucinations:
        all_ok = False
        hallucinated_ids = ", ".join(f"{h['check_id']} on {h['node_id']}" for h in hallucinations)
        checks.append({
            "check": "l1_no_hallucinations",
            "passed": False,
            "detail": f"Hallucinated L1 checks (not in known presets): {hallucinated_ids}",
            "hallucinations": hallucinations,
        })
    else:
        checks.append({
            "check": "l1_no_hallucinations",
            "passed": True,
            "detail": "All L1 checks reference known presets",
        })

    note = ""
    if not all_ok:
        failed = [c for c in checks if not c["passed"]]
        note = f"L1 structural: {len(failed)}/{len(checks)} checks failed"

    return PlanL1Result(passed=all_ok, checks=checks, note=note)


def plan_l2(
    dag: list[dict],
    plan_goal: str = "",
) -> PlanL2Result:
    """Run plan-level L2 rubric evaluation via the meta_planner LLM.

    This is the plan-structure gate (covers_goal, right_sized, deps_correct,
    measurable) — separate from the node-level L2 rubric judge.

    Args:
        dag: List of node dicts from the plan.
        plan_goal: The plan's goal text.

    Returns:
        ``PlanL2Result`` with weighted score and per-item judgments.
    """
    rubric = load_rubric("plan_structure") or {
        "name": "plan_structure",
        "items": [
            {"id": "covers_goal", "rubric_item": "Do the nodes together fully cover the plan goal?", "weight": 2.0},
            {"id": "right_sized", "rubric_item": "Is each node a bounded, single-responsibility unit?", "weight": 1.5},
            {"id": "deps_correct", "rubric_item": "Are dependencies sequential and correctly ordered? Each node must depend on the previous (no parallel branches).", "weight": 1.5},
            {"id": "measurable", "rubric_item": "Does each node have a measurable success criterion appropriate to its domain? Code nodes need deterministic checks; design/visual nodes may use rubric-based quality checks.", "weight": 1.0},
            {"id": "checks_scoped", "rubric_item": "Is each node's checks scoped to its task (no irrelevant checks on unrelated nodes)?", "weight": 1.0},
            {"id": "staffing_capable", "rubric_item": "Is each node staffed by an agent_config actually capable of its task (no strategic-operational mismatch)?", "weight": 2.0},
            {"id": "checks_match_capabilities", "rubric_item": "Do each node's checks match its capabilities' dimensions (objective -> L1, subjective -> L2)?", "weight": 1.5},
        ],
    }

    # Augment with domain standard rubric item (planning standard)
    try:
        from backend.planning.planning_standard import get_gate_rubric_item
        extra = get_gate_rubric_item()
        if not any(item["id"] == extra["id"] for item in rubric["items"]):
            rubric["items"].append(extra)
    except Exception:
        pass

    plan_nodes = "\n".join(
        json.dumps({
            "id": n.get("id", "?"),
            "task": n.get("task", {}).get("text", ""),
            "success": n.get("success", {}).get("text", ""),
            "depends_on": n.get("depends_on", []),
            "checks": [
                {"id": c.get("id", "?"), "type": c.get("type", "?"), "criterion": c.get("criterion", "")}
                for c in (n.get("checks") or [])
            ],
        }, indent=2)
        for n in dag
    )

    rubric_text = "\n".join(
        f"- {item['id']} (weight {item.get('weight', 1.0)}): {item['rubric_item']}"
        for item in rubric["items"]
    )

    staffing_text = "\n".join(
        f"  {n.get('id', '?')}: member={n.get('members', [{}])[0].get('agent_config', '?')}, "
        f"node_caps={n.get('capabilities', [])}, task={n.get('task', {}).get('text', '')[:100]}"
        for n in dag
    )

    prompt = PLAN_JUDGE_PROMPT.format(
        plan_goal=plan_goal or "(not provided)",
        plan_nodes=plan_nodes,
        staffing=staffing_text or "(none)",
        rubric=rubric_text,
    )

    try:
        resp, raw_text = call_llm_structured(
            prompt, PlanJudgeResponse, model_cfg=None,
            role="l2_judge", include_raw=True,
        )
    except Exception as exc:
        logger.warning("Plan L2 LLM call failed: %s — returning score 0", exc)
        return PlanL2Result(score=0.0)

    judgments: list[dict] = []
    hard_failures: list[dict] = []
    judged_map = {judged.id: judged for judged in resp.items}

    total_weight = 0.0
    met_weight = 0.0
    for item in rubric["items"]:
        item_id = item["id"]
        weight = item.get("weight", 1.0)
        judged = judged_map.get(item_id)
        met = bool(judged.met) if judged else False
        detail = "met" if judged else "missing from L2 response"
        what = getattr(judged, "what", None) or ""
        where = getattr(judged, "where", None) or ""
        why = getattr(judged, "why", None) or ""
        how = getattr(judged, "how", None) or ""
        total_weight += weight
        if met:
            met_weight += weight
        judgment = {
            "id": item_id, "met": met, "weight": weight, "detail": detail,
            "what": what, "where": where, "why": why, "how": how,
        }
        judgments.append(judgment)
        if not met:
            hard_failures.append(judgment)

    score = met_weight / total_weight if total_weight > 0 else 0.0
    return PlanL2Result(
        score=score, judgments=judgments, hard_failures=hard_failures,
        raw_response=raw_text,
    )


def evaluate_plan(
    dag: list[dict],
    plan_goal: str = "",
    l2_threshold: float = 0.7,
) -> PlanEvalResult:
    """Run full plan evaluation (L1 structural + staffing + L2 plan rubric).

    Args:
        dag: List of node dicts from the plan.
        plan_goal: The plan's goal text (for L2 context).
        l2_threshold: Minimum L2 score to consider the plan passing.

    Returns:
        ``PlanEvalResult`` with L1, staffing, and L2 results.
    """
    l1 = run_plan_l1(dag)
    if not l1.passed:
        return PlanEvalResult(
            l1=l1, l2=None, plan_goal_review=0.0, passed=False,
        )

    # Staffing L1 check (deterministic, catches obvious mismatches)
    staffing_fails = staffing_l1(dag)
    if staffing_fails:
        fail_detail = "; ".join(staffing_fails)
        l1.checks.append({
            "check": "staffing_l1",
            "passed": False,
            "detail": fail_detail,
            "staffing_failures": staffing_fails,
        })
        l1.passed = False
        l1.note = f"L1 staffing: {len(staffing_fails)} mismatch(es)"
        return PlanEvalResult(
            l1=l1, l2=None, plan_goal_review=0.0, passed=False,
        )

    l2_result = plan_l2(dag, plan_goal)
    score = l2_result.score
    passed = score >= l2_threshold and not l2_result.hard_failures

    return PlanEvalResult(
        l1=l1,
        l2=l2_result,
        plan_goal_review=round(score, 4),
        passed=passed,
        hard_failures=l2_result.hard_failures,
    )


# ── Plan gate / revise loop ────────────────────────────────────────

MAX_PLAN_REVISIONS = 2
PLAN_GATE_THRESHOLD = 0.7


@dataclass
class PlanGateDecision:
    action: str
    plan_goal_review: float = 0.0
    reason: dict | None = None
    feedback_text: str = ""
    l2_judgments: list[dict] = field(default_factory=list)
    hard_failures: list[dict] = field(default_factory=list)
    raw_response: str | None = None


def gate_plan(dag: list[dict], plan_goal: str = "", threshold: float = PLAN_GATE_THRESHOLD) -> PlanGateDecision:
    result = evaluate_plan(dag, plan_goal, threshold)
    if not result.l1.passed:
        l1_failures = [c for c in result.l1.checks if not c.get("passed", False)]
        fail_details = "; ".join(f"{c.get('check', '?')}: {c.get('detail', '')}" for c in l1_failures)

        # Check if staffing was the reason — give specific guidance
        staffing_fails = [c for c in l1_failures if c.get("check") == "staffing_l1"]
        if staffing_fails:
            feedback_text = (
                f"Plan L1 staffing check failed: {fail_details}. "
                "Reassign nodes to capable agent_configs (with matching capabilities) or "
                "request generation of a new agent_config if none fits."
            )
        else:
            feedback_text = f"Plan L1 structural check failed: {fail_details}"

        return PlanGateDecision(
            action="revise",
            reason={"L1": l1_failures},
            feedback_text=feedback_text,
        )
    if not result.passed:
        score = result.plan_goal_review
        l2_judgments = result.l2.judgments if result.l2 else []
        hard_failures = result.hard_failures
        # Validate hard_failure feedback against DimFeedback; mark degraded if filler
        for hf in hard_failures:
            hf_fb = {k: hf.get(k, "") for k in ("what", "where", "why", "how")}
            validated, _ = validate_feedback(hf_fb)
            if validated is None and hf_fb.get("what"):
                hf["detail"] = hf.get("detail", "") + " [feedback degraded]"
        if hard_failures:
            reason = {"L2": "hard gate failed", "score": score, "hard_failures": hard_failures, "judgments": l2_judgments}
            parts: list[str] = []
            for f in hard_failures:
                detail = f.get('detail', 'not met')
                degraded = "degraded" in detail
                what = f.get('what', '').strip()
                why = f.get('why', '').strip()
                msg = f"{f.get('id')}: not met"
                if what:
                    msg += f" — {what}"
                if why and not what:
                    msg += f" ({why})"
                if degraded:
                    msg += " [feedback degraded]"
                parts.append(msg)
            feedback = "Plan quality hard gate failed: " + "; ".join(parts)
        else:
            reason = {"L2": "below threshold", "score": score, "judgments": l2_judgments}
            feedback = f"Plan quality review score ({score:.2f}) is below required threshold ({threshold:.2f}). Review and refine the plan decomposition."
        return PlanGateDecision(
            action="revise",
            plan_goal_review=score,
            reason=reason,
            feedback_text=feedback,
            l2_judgments=l2_judgments,
            hard_failures=hard_failures,
        )
    return PlanGateDecision(
        action="ratify",
        plan_goal_review=result.plan_goal_review,
        l2_judgments=result.l2.judgments if result.l2 else [],
        raw_response=result.l2.raw_response if result.l2 else None,
    )


def run_plan_gate(plan_data: dict, max_rounds: int = MAX_PLAN_REVISIONS) -> PlanGateDecision:
    dag = plan_data.get("nodes", []) or plan_data.get("dag", [])
    plan_goal = plan_data.get("goal", "")
    normalized_dag = []
    for n in dag:
        node = dict(n)
        if "id" not in node and "node_id" in node:
            node["id"] = node["node_id"]
        normalized_dag.append(node)
    decision: PlanGateDecision = PlanGateDecision(action="revise", feedback_text="No gate rounds attempted")
    for _ in range(max_rounds):
        decision = gate_plan(normalized_dag, plan_goal)
        if decision.action == "ratify":
            return decision
        plan_data["gate_feedback"] = decision.reason
        plan_data["gate_feedback_text"] = decision.feedback_text
    plan_data["gate_exhausted"] = True
    return decision
