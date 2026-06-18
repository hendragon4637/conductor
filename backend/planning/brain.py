from __future__ import annotations

import json
import os
import urllib.request
from typing import Any, Callable

from backend.planning.schema import Plan, PlanNode, NodeMember, TaskSpec, NodeSuccess, SuccessCriterion
from backend.planning.spec import validate_plan as validate_plan_spec
from backend.planning.model_selector import select_brain_model, budget_available


# Default brain LLM endpoint — a local OpenAI-compatible server.
BRAIN_ENDPOINT = os.environ.get(
    "BRAIN_ENDPOINT",
    "http://127.0.0.1:11434/v1/chat/completions",
)
BRAIN_MODEL = os.environ.get(
    "BRAIN_MODEL",
    "Qwen_Qwen3.5-9B-Q4_K_M.gguf",
)


def _call_llm(prompt: str, max_tokens: int = 8192) -> str:
    """Call the brain LLM and return the raw response text.

    Includes budget-aware system prompt and truncation guard:
    - Raises max_tokens cap to 8192 (was 4096)
    - Tells the model its output budget upfront
    - Detects ``finish_reason == "length"`` and retries with higher cap
    """
    model_cfg = select_brain_model(task_hint=prompt[:100])
    endpoint = model_cfg["endpoint"].rstrip("/")
    if not endpoint.endswith("/chat/completions"):
        endpoint += "/chat/completions"

    budget_prompt = (
        f"You have a maximum of {max_tokens} output tokens. Produce a COMPLETE, valid response within this budget. "
        "If the task is large, prioritize structural completeness (valid JSON / all required fields) over prose. "
        "Never stop mid-structure."
    )

    body = {
        "model": model_cfg["model"],
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a technical plan decomposition engine. "
                    "You output ONLY valid JSON matching the requested schema. "
                    "Never include explanations, markdown fences, or extra text. "
                ) + budget_prompt,
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "max_tokens": max_tokens,
    }
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        result = json.loads(resp.read())
    content = result["choices"][0]["message"]["content"]

    finish_reason = result["choices"][0].get("finish_reason", "")
    if finish_reason == "length":
        retry_prompt = prompt + "\n\nYour previous response was truncated. Return a MORE COMPACT response."
        body["messages"].append({"role": "assistant", "content": content})
        body["messages"].append({"role": "user", "content": retry_prompt})
        body["max_tokens"] = max_tokens * 2
        req = urllib.request.Request(
            endpoint,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=120) as resp2:
            result2 = json.loads(resp2.read())
        content = result2["choices"][0]["message"]["content"]

    return content


def _default_llm(prompt: str) -> str:
    """Legacy wrapper — calls _call_llm with default max_tokens."""
    return _call_llm(prompt, max_tokens=8192)


def propose_plan(
    user_intent: str,
    context: dict[str, Any] | None = None,
    available_agent_configs: list[dict[str, Any]] | None = None,
    multimodal_refs: list[str] | None = None,
    llm_call: Callable[[str], str] | None = None,
) -> Plan:
    """Turn a user intent into a structured plan DAG.

    Args:
        user_intent: Free-text description of what the user wants done.
        context: Optional dict (e.g. ``{"project": "backend-api"}``) with
            additional context for the brain.
        available_agent_configs: List of agent config dicts with at least
            ``agent_config_id`` and ``role`` keys.  If omitted, a default
            list is used.
        multimodal_refs: Optional paths or URLs to reference images, etc.
        llm_call: Function that takes a prompt string and returns a JSON
            string.  Defaults to calling the local brain LLM endpoint.

    Returns:
        A validated ``Plan`` with at least 2 nodes.
    """
    if llm_call is None:
        llm_call = _default_llm

    cfgs = available_agent_configs or [
        {"agent_config_id": "opencode:backend-executor", "role": "executor"},
    ]
    ctx = context or {}

    cfg_lines = "\n".join(
        f"  - {c.get('agent_config_id', '?')} (role: {c.get('role', '?')})"
        for c in cfgs
    )

    prompt = (
        f"User intent: {user_intent}\n"
        f"Context: {json.dumps(ctx)}\n\n"
        f"Available agent configs:\n{cfg_lines}\n\n"
        f"Design a plan DAG with 2-5 nodes. "
        f"Default pattern: planner -> executor -> reviewer.\n"
        f"Each node must reference one of the available agent_configs.\n"
        f"Respond as a JSON object with \"nodes\" (array of objects) each with:\n"
        f"  id (str, unique within the plan),\n"
        f"  agent_config (str, exact agent_config_id from the list),\n"
        f"  role (str),\n"
        f"  depends_on (list of strings, referencing other node ids),\n"
        f"  success_text (str, measurable success criterion),\n"
        f"  project_id (str, use \"{ctx.get('project', 'default')}\")\n"
    )

    raw = llm_call(prompt)

    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        raw = raw.rsplit("```", 1)[0]

    data = json.loads(raw)
    if "nodes" not in data:
        raise ValueError(f"LLM response missing 'nodes' key: {raw[:200]}")

    project_id = ctx.get("project", "")
    backend_default = ctx.get("backend", "opencode")
    dag = []
    for n in data["nodes"]:
        dag.append(PlanNode(
            id=n["id"],
            members=[NodeMember(
                agent_config=n.get("agent_config", "opencode:backend-executor"),
                backend=n.get("backend", backend_default),
                role=n.get("role", "executor"),
            )],
            depends_on=n.get("depends_on", []),
            task=TaskSpec(text="", inputs=[], deliverables=[]),
            success=NodeSuccess(text=n.get("success_text", n.get("success", "Complete the task"))),
            project_id=n.get("project_id", project_id),
        ))

    plan_id = _generate_plan_id(user_intent, project_id)
    return Plan(
        plan_id=plan_id,
        project_id=project_id,
        user_intent=user_intent,
        goal=ctx.get("goal", user_intent),
        dag=dag,
    )


def refine_plan(
    plan_data: dict,
    instruction: str,
    image_data: str | None = None,
    llm_call: Callable[[str], str] | None = None,
) -> dict | None:
    """Refine an existing plan's nodes based on a natural-language instruction.

    Calls the brain LLM with the current plan DAG and the user's refinement
    instruction. Returns the updated plan dict, or *None* if refinement fails
    (the caller should preserve the original plan).
    """
    if llm_call is None:
        llm_call = _default_llm

    existing_nodes = plan_data.get("nodes", [])
    prompt = (
        f"Existing plan title: {plan_data.get('title', '')}\n"
        f"Existing plan description: {plan_data.get('description', '')}\n\n"
        f"Current plan nodes (JSON):\n{json.dumps(existing_nodes, indent=2)}\n\n"
        f"Refinement instruction: {instruction}\n\n"
        f"{'The user also attached an image for VLM review (not available to you as text).' if image_data else ''}\n\n"
        f"Respond as a JSON object with one key \"nodes\" whose value is an array of objects, "
        f"each with:\n"
        f"  node_id (str, unique within the plan, preserve existing IDs when possible),\n"
        f"  title (str),\n"
        f"  description (str),\n"
        f"  depends_on (list of strings, referencing other node ids),\n"
        f"  status (str, use 'pending'),\n"
        f"  agent_config_id (str or null),\n"
        f"  success_criterion (str or null)\n"
    )

    try:
        raw = llm_call(prompt)
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1]
            raw = raw.rsplit("```", 1)[0]

        data = json.loads(raw)
        if "nodes" not in data:
            return None

        plan_data["nodes"] = data["nodes"]
        return plan_data
    except Exception:
        import traceback
        traceback.print_exc()
        return None


def propose_plan_v2(
    user_intent: str,
    context: dict[str, Any] | None = None,
    available_agent_configs: list[dict[str, Any]] | None = None,
    available_tools: set[str] | None = None,
    multimodal_refs: list[str] | None = None,
    llm_call: Callable[[str], str] | None = None,
) -> SpecPlan:
    """Turn a user intent into a spec-valid plan using model_selector.

    Uses the spec (File 17.3) schema with three-tier typing, formal
    success criteria, and comprehensive validation.

    Args:
        user_intent: Free-text description.
        context: Optional dict with project name etc.
        available_agent_configs: List of agent config dicts.
        available_tools: Set of tool names available for ``tool``-kind nodes.
        multimodal_refs: Optional paths or URLs to reference images.
        llm_call: Function that takes a prompt and returns JSON.

    Returns:
        A validated ``SpecPlan``.
    """
    if llm_call is None:
        llm_call = _default_llm

    cfgs = available_agent_configs or [
        {"agent_config_id": "opencode:backend-executor", "role": "executor"},
    ]
    ctx = context or {}

    cfg_lines = "\n".join(
        f"  - {c.get('agent_config_id', '?')} (role: {c.get('role', '?')})"
        for c in cfgs
    )
    tool_lines = "\n".join(f"  - {t}" for t in (available_tools or []))
    three_tier_rule = (
        "Three-tier rule:\n"
        "  - 'tool': flowchartable, no LLM needed (run tests, git, scaffold, format)\n"
        "  - 'single_agent': flowchartable, one LLM call (summarize, classify, translate)\n"
        "  - 'team': NOT flowchartable (design feature, debug novel failure, ambiguous build)\n"
        "Prefer 'tool' or 'single_agent' over 'team' when possible."
    )

    prompt = (
        f"User intent: {user_intent}\n"
        f"Context: {json.dumps(ctx)}\n\n"
        f"Available agent configs:\n{cfg_lines}\n\n"
        f"{'Available tools:\n' + tool_lines if tool_lines else ''}\n\n"
        f"{three_tier_rule}\n\n"
        f"Design a plan DAG with 2-5 nodes. "
        f"Respond as a JSON object with these keys:\n"
        f"  nodes (array of objects, each with):\n"
        f"    - id (str, unique)\n"
        f"    - kind ('tool' | 'single_agent' | 'team')\n"
        f"    - ref (str: tool name or agent_config_id)\n"
        f"    - role (str or null)\n"
        f"    - project_id (str, use \"{ctx.get('project', 'default')}\")\n"
        f"    - depends_on (list of strings)\n"
        f"    - success (object with: text=str, deterministic_checks=list[str])\n"
        f"  worktree_decision (object with: project=str, create_new_repo=bool, branch=str)\n"
        f"  plan_id (str, auto-generated)\n"
    )

    raw = llm_call(prompt)

    # Strip any markdown fences
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        raw = raw.rsplit("```", 1)[0]

    data = json.loads(raw)
    if "nodes" not in data:
        raise ValueError(f"LLM response missing 'nodes' key: {raw[:200]}")

    # Parse nodes into spec schema
    spec_nodes = []
    for n in data["nodes"]:
        success_data = n.get("success", {})
        if isinstance(success_data, str):
            success_data = {"text": success_data, "deterministic_checks": []}
        spec_nodes.append(SpecPlanNode(
            id=n["id"],
            kind=n.get("kind", "team"),
            ref=n.get("ref", n.get("agent_config", "")),
            role=n.get("role"),
            project_id=n.get("project_id", ctx.get("project", "default")),
            depends_on=n.get("depends_on", []),
            success=SuccessCriterion(**success_data),
            new_agent_config=n.get("new_agent_config"),
        ))

    project_id = ctx.get("project", "")
    plan_id = data.get("plan_id", _generate_plan_id(user_intent, project_id))
    spec_plan = SpecPlan(
        plan_id=plan_id,
        user_intent=user_intent,
        worktree_decision=data.get("worktree_decision", {
            "project": ctx.get("project", "default"),
            "create_new_repo": False,
            "branch": "main",
        }),
        nodes=spec_nodes,
        multimodal_refs=multimodal_refs or [],
    )

    # Validate
    try:
        validate_plan_spec(spec_plan, tool_registry=available_tools)
    except ValueError as e:
        # Retry once on failure
        raw2 = llm_call(prompt + f"\n\nPrevious attempt failed validation: {e}. Fix the plan.")
        raw2 = raw2.strip()
        if raw2.startswith("```"):
            raw2 = raw2.split("\n", 1)[1]
            raw2 = raw2.rsplit("```", 1)[0]
        data2 = json.loads(raw2)
        # Re-parse and re-validate
        spec_nodes2 = []
        for n in data2.get("nodes", data["nodes"]):
            success_data = n.get("success", {})
            if isinstance(success_data, str):
                success_data = {"text": success_data, "deterministic_checks": []}
            spec_nodes2.append(SpecPlanNode(
                id=n["id"],
                kind=n.get("kind", "team"),
                ref=n.get("ref", n.get("agent_config", "")),
                role=n.get("role"),
                project_id=n.get("project_id", ctx.get("project", "default")),
                depends_on=n.get("depends_on", []),
                success=SuccessCriterion(**success_data),
            ))
        spec_plan = SpecPlan(
            plan_id=data2.get("plan_id", plan_id),
            user_intent=user_intent,
            worktree_decision=data2.get("worktree_decision", spec_plan.worktree_decision),
            nodes=spec_nodes2,
            multimodal_refs=multimodal_refs or [],
        )
        validate_plan_spec(spec_plan, tool_registry=available_tools)

    return spec_plan


def _generate_plan_id(intent: str, project_id: str = "") -> str:
    import hashlib
    payload = f"{project_id}::{intent}" if project_id else intent
    h = hashlib.sha256(payload.encode()).hexdigest()[:12]
    return f"plan-{h}"
