"""LangGraph state machine for the planner lifecycle.

formulate → inject → decompose → gate (with revise loop and clarify pause).
All backend logic delegates to the existing monolith modules.
"""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

MAX_CLARIFY_ROUNDS = 3
MAX_REVISE_ROUNDS = 3


class PlanState(TypedDict):
    raw_input: str
    origin: str
    meta_goal: dict | None
    dag: dict | None
    plan_goal_review: float | None
    clarify_rounds: int
    revise_rounds: int
    status: str
    error: str | None
    gate_feedback: str | None


def _n_formulate(state: PlanState) -> PlanState:
    """Call the LLM formulator, detect clarification needs."""
    from backend.planning.meta_planner.goal_formulator import formulate

    mg = formulate(raw_input=state["raw_input"], origin=state["origin"])
    merged = {**state, "meta_goal": mg.model_dump()}
    if mg.needs_clarification:
        merged["status"] = "awaiting_clarification"
    else:
        merged["status"] = "formulated"
    return PlanState(**merged)


def _n_inject_conventions(state: PlanState) -> PlanState:
    """Inject domain conventions into the meta-goal."""
    from backend.planning.meta_planner.goal_formulator import (
        MetaGoal,
        enrich_with_conventions,
    )

    mg = MetaGoal(**state["meta_goal"]) if isinstance(state["meta_goal"], dict) else state["meta_goal"]
    enriched = enrich_with_conventions(mg, state["raw_input"])
    return PlanState(**{**state, "meta_goal": enriched.model_dump()})


def _n_decompose(state: PlanState) -> PlanState:
    """Decompose goal into DAG (no capabilities yet — those come via selector).

    On revision cycles (gate → decompose) the gate feedback and prior DAG
    are passed through so the LLM can fix specific failures rather than
    regenerating from scratch.
    """
    from backend.planning.meta_planner.decomposer import decompose
    from backend.planning.meta_planner.split import split_oversized

    mg = state["meta_goal"]
    # On revision cycles, pass gate feedback + prior DAG to the decomposer
    # so the LLM can fix specific failures rather than regenerating from scratch.
    prior_dag_raw = state.get("dag")
    prior_dag = prior_dag_raw if isinstance(prior_dag_raw, list) else None
    dag = decompose(
        goal=mg.get("goal", "") if isinstance(mg, dict) else str(mg),
        spec=mg.get("spec", "") if isinstance(mg, dict) else "",
        quality_intent=mg.get("quality_intent", "") if isinstance(mg, dict) else "",
        domain=mg.get("domain") if isinstance(mg, dict) else None,
        feedback=state.get("gate_feedback") or "",
        prior_dag=prior_dag if state.get("gate_feedback") else None,
    )
    quality = mg.get("quality_intent", "") if isinstance(mg, dict) else ""
    dag = split_oversized(dag, mg)

    node_dicts = []
    for n in dag.nodes:
        members_list = [
            {"agent_config": m.agent_config, "backend": m.backend, "role": m.role}
            for m in n.members
        ]
        backend = members_list[0].get("backend", "opencode") if members_list else "opencode"
        agent_config_id = members_list[0]["agent_config"] if members_list else "opencode:backend-executor"
        task_text = n.task.text
        node_dicts.append({
            "id": n.id,
            "depends_on": n.depends_on,
            "members": members_list,
            "backend": backend,
            "agent_config_id": agent_config_id,
            "task": {"text": task_text, "inputs": n.task.inputs, "deliverables": n.task.deliverables},
            "success": {"text": n.success.text},
            "capabilities": [],
            "checks": [],
            "project_id": "default",
        })
    return PlanState(**{**state, "dag": node_dicts, "quality_intent": quality})


def _n_select_capabilities(state: PlanState) -> PlanState:
    """Resolve capabilities for every DAG node via the capability selector.

    Skips nodes that already have non-empty ``capabilities`` pre-set
    (BYO-DAG path).  Nodes without capabilities get them resolved by the LLM
    capability selector as usual.
    """
    dag_list = state["dag"]
    if isinstance(dag_list, dict):
        dag_list = dag_list.get("nodes", [dag_list])

    if not dag_list:
        return PlanState(**{**state, "dag": dag_list})

    # Only call the LLM selector for nodes without pre-set capabilities.
    need_resolve = [n for n in dag_list if not (n.get("capabilities") or [])]
    if need_resolve:
        from backend.planning.capability.selector import resolve_dag_capabilities
        # resolve_dag_capabilities mutates dicts in-place
        resolve_dag_capabilities(need_resolve)

    return PlanState(**{**state, "dag": dag_list})


def _n_generate_checks(state: PlanState) -> PlanState:
    """Generate evaluation checks from capability dimensions (objective->L1, subjective->L2)."""
    from backend.planning.capability.checkgen import generate_capability_checks

    dag_list = state["dag"]
    if isinstance(dag_list, dict):
        dag_list = dag_list.get("nodes", [dag_list])

    for node in dag_list:
        generated = generate_capability_checks(node)
        node["checks"] = generated

    return PlanState(**{**state, "dag": dag_list})


def _n_gate(state: PlanState) -> PlanState:
    """Evaluate plan via L1 + L2; decide ratify / revise / escalate."""
    from backend.evaluator.plan_evaluator import gate_plan

    # dag is now a list of node dicts from _n_decompose
    dag_list = state["dag"]
    if isinstance(dag_list, dict):
        dag_list = dag_list.get("nodes", [dag_list])

    goal_text = ""
    mg = state.get("meta_goal") or {}
    if isinstance(mg, dict):
        goal_text = mg.get("goal", "")
    elif hasattr(mg, "goal"):
        goal_text = mg.goal

    dec = gate_plan(dag_list, plan_goal=goal_text)
    if dec.action == "ratify":
        return PlanState(**{
            **state,
            "plan_goal_review": dec.plan_goal_review,
            "status": "gated_ok",
            "gate_feedback": None,
        })

    return PlanState(**{
        **state,
        "status": "revise",
        "revise_rounds": (state.get("revise_rounds") or 0) + 1,
        "gate_feedback": dec.feedback_text,
        "error": dec.feedback_text,
    })


def _route_entry(state: PlanState) -> str:
    """Route entry: skip to inject if already formulated (resume after clarify)."""
    if state.get("status") == "formulated":
        return "inject"
    return "formulate"


def _route_after_formulate(state: PlanState) -> str:
    if state["status"] == "awaiting_clarification":
        return "await"
    return "inject"


def _route_after_gate(state: PlanState) -> str:
    if state["status"] == "gated_ok":
        return "ratify"
    if (state.get("revise_rounds") or 0) < MAX_REVISE_ROUNDS:
        return "revise"
    return "escalate"


def build_planner_graph() -> StateGraph:
    """Build and return the compiled planner LangGraph."""
    g = StateGraph(PlanState)

    g.add_node("formulate", _n_formulate)
    g.add_node("inject", _n_inject_conventions)
    g.add_node("decompose", _n_decompose)
    g.add_node("select_capabilities", _n_select_capabilities)
    g.add_node("generate_checks", _n_generate_checks)
    g.add_node("gate", _n_gate)

    g.set_conditional_entry_point(
        _route_entry,
        {"formulate": "formulate", "inject": "inject"},
    )

    g.add_conditional_edges(
        "formulate",
        _route_after_formulate,
        {"await": END, "inject": "inject"},
    )
    g.add_edge("inject", "decompose")
    g.add_edge("decompose", "select_capabilities")
    g.add_edge("select_capabilities", "generate_checks")
    g.add_edge("generate_checks", "gate")
    g.add_conditional_edges(
        "gate",
        _route_after_gate,
        {"ratify": END, "revise": "decompose", "escalate": END},
    )

    checkpointer = MemorySaver()
    return g.compile(checkpointer=checkpointer)
