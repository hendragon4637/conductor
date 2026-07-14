"""LangGraph state machine for the planner lifecycle.

formulate → inject → generate_plan (spawn meta-planner) → [async: observed→validate→gate] → revise|ratify|escalate.
Decompose is now ASYNC — the meta-planner agent writes .plan/ files, watcher observes,
planner-svc consumes ``node.observed`` to assemble, validate, gate, and either retry or ratify.
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
    planning_session: str | None  # node_session id for the planning session
    plan_id: str | None
    project_id: str | None


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


def _cancel_conv_for_steering(aionui_host: str, conv_id: str) -> None:
    """Cancel any running task on an AionUi conversation so steering can send a message.

    AionUi rejects POST /messages with 409 when the conversation is still running
    (``can_send_message=false``).  This helper calls the /cancel endpoint with the
    current turn_id and polls until the conversation becomes idle.
    """
    import json
    import time as _time
    import urllib.request
    import urllib.error

    _logger = __import__("logging").getLogger(__name__)
    try:
        # 1. Fetch current runtime state to get turn_id
        get_req = urllib.request.Request(
            f"{aionui_host}/api/conversations/{conv_id}",
            method="GET",
        )
        with urllib.request.urlopen(get_req, timeout=15) as resp:
            conv_data = json.loads(resp.read().decode())
        runtime = conv_data.get("data", {}).get("runtime", {})
        state = runtime.get("state", "")
        if state != "running":
            _logger.debug("Conv %s not running (state=%s) — skip cancel", conv_id, state)
            return

        turn_id = runtime.get("turn_id")
        if not turn_id:
            _logger.warning("Conv %s has no turn_id — cannot cancel", conv_id)
            return

        # 2. POST /cancel with turn_id
        cancel_payload = json.dumps({"turn_id": turn_id}).encode()
        cancel_req = urllib.request.Request(
            f"{aionui_host}/api/conversations/{conv_id}/cancel",
            data=cancel_payload, method="POST",
        )
        cancel_req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(cancel_req, timeout=15) as resp:
            cancel_resp = json.loads(resp.read().decode())
        _logger.info(
            "Cancel sent for conv=%s turn=%s → %s",
            conv_id, turn_id, cancel_resp.get("data", {}).get("runtime", {}).get("state", "?"),
        )

        # 3. Poll until idle (can_send_message=true)
        for attempt in range(30):
            _time.sleep(1)
            poll_req = urllib.request.Request(
                f"{aionui_host}/api/conversations/{conv_id}",
                method="GET",
            )
            try:
                with urllib.request.urlopen(poll_req, timeout=10) as poll_resp:
                    poll_data = json.loads(poll_resp.read().decode())
                poll_runtime = poll_data.get("data", {}).get("runtime", {})
                if poll_runtime.get("state") == "idle" and poll_runtime.get("can_send_message") is True:
                    _logger.info("Conv %s now idle after cancel", conv_id)
                    return
            except Exception:
                _logger.debug("Poll attempt %d failed for conv %s", attempt + 1, conv_id)
        _logger.warning("Conv %s did not become idle within 30s after cancel — proceeding anyway", conv_id)
    except urllib.error.HTTPError as e:
        body = e.read().decode() if e.fp else ""
        _logger.warning("Cancel failed for conv %s: HTTP %d %s", conv_id, e.code, body)


def _n_generate_plan(state: PlanState) -> PlanState:
    """Spawn the meta-planner agent in a planning worktree (replaces n_decompose).

    The meta-planner writes .plan/ files; the watcher polls the AionUi
    conversation; planner-svc consumes ``node.observed`` to assemble,
    validate, gate, and retry.  NO LLM call in this LangGraph node.
    """
    import json
    import os
    import time
    from uuid import uuid4

    from backend.aionui.client import AionUiClient
    from backend.db.queries import get_agent_config
    from backend.planning.harness_worktree import (
        MAX_PLANNING_ATTEMPTS,
        create_planning_worktree,
        planning_brief,
        retry_brief,
    )
    from backend.worktree.manager import WorktreeManager
    from shared.outbox import emit
    from shared.db import session as db_session
    from contracts.events import NodeSpawned

    mg = state["meta_goal"]
    if not isinstance(mg, dict):
        mg = {}
    plan_id = state.get("plan_id") or f"plan_{uuid4().hex[:8]}"
    project_id = state.get("project_id") or mg.get("project_id", "default")
    workspace_root = os.environ.get("WORKSPACE_ROOT", "/opt/aipc/conductor/workspace")

    # 1. Create or reuse planning worktree
    wm = WorktreeManager(workspace_root)
    wt = create_planning_worktree(plan_id, project_id, workspace_root)

    # 2. Build brief (with retry feedback if this is a re-spawn)
    prior_feedback_raw = state.get("gate_feedback")
    prior_feedback: list[str] = [] if not prior_feedback_raw else [prior_feedback_raw]
    if prior_feedback:
        try:
            brief = retry_brief(prior_feedback, mg, wt)
        except Exception:
            logger = __import__("logging").getLogger(__name__)
            logger.exception("retry_brief LLM failed — falling back to planning_brief")
            brief = planning_brief(mg, wt)
    else:
        brief = planning_brief(mg, wt)

    # 3. Create placeholder plan + run + node_session with role='planning'
    from backend.db.queries import conn as db_conn
    run_id = f"plan_{plan_id}"
    ns_id = f"ns_plan_{uuid4().hex[:12]}"
    with db_conn() as conn:
        conn.execute(
            """INSERT INTO plans (plan_id, project_id, user_intent, goal)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (plan_id) DO UPDATE SET goal = EXCLUDED.goal""",
            (plan_id, project_id, state.get("raw_input", mg.get("goal", "")), mg.get("goal", "")),
        )
        conn.execute(
            """INSERT INTO runs (id, plan_id, state)
               VALUES (%s, %s, 'planning')
               ON CONFLICT (id) DO NOTHING""",
            (run_id, plan_id),
        )
        conn.execute(
            """INSERT INTO node_sessions (id, run_id, node_id, role, backend, attempt, worktree, members)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (ns_id, run_id, "_planning", "planning", "opencode",
             state.get("revise_rounds", 0) + 1, wt, json.dumps([])),
        )

    # 4. Check steering: reuse previous conversation if within limit
    aionui_host = os.environ.get("AIONUI_HOST", "http://127.0.0.1:40937")
    aionui = AionUiClient(aionui_host)
    cfg = get_agent_config("meta-planner")
    if not cfg:
        raise ValueError("meta-planner agent_config not found in DB")
    reuse_conv_id: str | None = None
    reuse_steer_count = 0
    if prior_feedback:
        with db_conn() as conn:
            row = conn.execute(
                """SELECT aionui_conversation_id, steering_count
                     FROM node_sessions
                    WHERE run_id = %s AND node_id = %s AND role = 'planning'
                      AND aionui_conversation_id IS NOT NULL
                    ORDER BY created_at DESC LIMIT 1""",
                (run_id, "_planning"),
            ).fetchone()
            if row:
                prev_conv = row["aionui_conversation_id"]
                prev_steer = row["steering_count"] or 0
                if prev_conv and prev_steer < 5:
                    reuse_conv_id = str(prev_conv)
                    reuse_steer_count = int(prev_steer or 0)

    if reuse_conv_id:
        conv_id = reuse_conv_id
        import urllib.request
        import urllib.error

        # Cancel any running task on this conversation before steering
        _cancel_conv_for_steering(aionui_host, conv_id)
        _logger = __import__("logging").getLogger(__name__)

        brief_payload = json.dumps({"content": brief}).encode()
        try:
            req = urllib.request.Request(
                f"{aionui_host}/api/conversations/{conv_id}/messages",
                data=brief_payload, method="POST",
            )
            req.add_header("Content-Type", "application/json")
            urllib.request.urlopen(req)
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"Failed to send steering brief to AionUi: {e.read().decode()}") from e
        _logger.info(
            "Planning steering: reusing conv=%s for plan=%s (steering_count=%d)",
            conv_id, plan_id, reuse_steer_count + 1,
        )
    else:
        conv_id = aionui.create_conversation(
            preset_agent_type="acp",
            assistant_id=cfg.get("assistant_id"),
            workspace=wt,
            model=cfg.get("model_preference"),
        )
        import urllib.request
        import urllib.error
        brief_payload = json.dumps({"content": brief}).encode()
        try:
            req = urllib.request.Request(
                f"{aionui_host}/api/conversations/{conv_id}/messages",
                data=brief_payload, method="POST",
            )
            req.add_header("Content-Type", "application/json")
            urllib.request.urlopen(req)
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"Failed to send brief to AionUi: {e.read().decode()}") from e

    # 5. Update node_session with conversation_id, steering_count, and plan record
    steering_count = reuse_steer_count + 1 if reuse_conv_id else 0
    with db_conn() as conn:
        conn.execute(
            """UPDATE node_sessions
                  SET aionui_conversation_id = %s, steering_count = %s
                WHERE id = %s""",
            (conv_id, steering_count, ns_id),
        )
        conn.execute(
            """UPDATE plans SET planning_worktree = %s, planning_status = 'generating',
               planning_attempts = COALESCE(planning_attempts, 0) + 1,
               partial_meta_goal = %s WHERE plan_id = %s""",
            (wt, json.dumps(mg), plan_id),
        )

    # 6. Emit node.spawned so the watcher picks it up
    with db_session() as s:
        emit(s, NodeSpawned(
            node_session_id=ns_id,
            backend="opencode",
            backend_ref=conv_id,
            worktree=wt,
            ts=time.time(),
        ))
        s.commit()

    return PlanState(**{
        **state,
        "plan_id": plan_id,
        "status": "generating",
        "planning_session": ns_id,
        "error": None,
    })


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


def _route_after_generate(state: PlanState) -> str:
    """After generate_plan: if generating async, end graph; otherwise continue."""
    if state["status"] == "generating":
        return "pause"
    return "select_capabilities"


def _route_after_gate(state: PlanState) -> str:
    if state["status"] == "gated_ok":
        return "ratify"
    if (state.get("revise_rounds") or 0) < MAX_REVISE_ROUNDS:
        return "revise"  # re-spawns via _n_generate_plan with feedback
    return "escalate"


def build_planner_graph() -> StateGraph:
    """Build and return the compiled planner LangGraph.

    Graph: formulate → inject → generate_plan → [pause|select_capabilities]
           → generate_checks → gate → revise|ratify|escalate.

    Decompose is now ASYNC — ``generate_plan`` spawns a meta-planner agent
    that writes .plan/ files.  The graph PAUSES at ``generate_plan`` when
    status is "generating".  planner-svc's ``on_node_observed_planning``
    handler does the assemble → validate → selector → check-gen → gate cycle
    asynchronously.  On ratify the DAG is persisted.  On failure the retry
    loop re-enters ``generate_plan`` with file-targeted feedback.
    """
    g = StateGraph(PlanState)

    g.add_node("formulate", _n_formulate)
    g.add_node("inject", _n_inject_conventions)
    g.add_node("generate_plan", _n_generate_plan)
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
    g.add_edge("inject", "generate_plan")
    # After generate_plan: pause (async cycle) or continue synchronously
    g.add_conditional_edges(
        "generate_plan",
        _route_after_generate,
        {"pause": END, "select_capabilities": "select_capabilities"},
    )
    g.add_edge("select_capabilities", "generate_checks")
    g.add_edge("generate_checks", "gate")
    g.add_conditional_edges(
        "gate",
        _route_after_gate,
        {"ratify": END, "revise": "generate_plan", "escalate": END},
    )

    checkpointer = MemorySaver()
    return g.compile(checkpointer=checkpointer)
