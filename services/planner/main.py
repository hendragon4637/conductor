"""planner-svc entrypoint.

FastAPI endpoints for goal submission, clarification, and ratification.
Background consumer for run.completed, run.failed, and node.observed (role=planning).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from contracts.events import PlanRatified, RunCompleted, RunFailed
from shared.bus import EventBus
from shared.config import ServiceConfig
from shared.db import init_db
from shared.models import NodeSession

from services.planner.graph import build_planner_graph

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

cfg = ServiceConfig.from_env()
bus = EventBus(cfg)
planner_graph = build_planner_graph()


# ── Request / response models ────────────────────────────────────────────


class GoalRequest(BaseModel):
    raw_input: str
    origin: str = "human"
    spec: str | None = None
    quality_intent: str | None = None
    nodes: list[dict] | None = None
    project_id: str = "default"


class ClarifyRequest(BaseModel):
    answer: str


class RatifyResponse(BaseModel):
    status: str
    plan_id: str
    run_id: str | None = None
    plan_goal_review: float | None = None
    gate_feedback: str | None = None


# ── Event handlers ───────────────────────────────────────────────────────


def _handle_run_completed(session, payload):
    logger.info("Run completed: %s", payload.get("run_id"))


def _handle_run_failed(session, payload):
    logger.warning("Run failed: %s", payload.get("run_id"))


def _on_node_observed_planning(session, payload):
    """Handle ``node.observed`` for role=planning sessions.

    Flow: assemble_plan → validate_assembled (pydantic+roster) → capability
    selector → check gen → gate.  On gate_ok the DAG is persisted and the
    plan is ready for ratification.  On failure the retry loop re-spawns
    the meta-planner with verbatim file-targeted feedback.
    """
    from contracts.plan_assembler import assemble_plan, validate_assembled
    from backend.planning.harness_worktree import (
        MAX_PLANNING_ATTEMPTS,
        on_planning_failed,
    )

    node_session_id = payload["node_session_id"]
    ns: NodeSession | None = (
        session.query(NodeSession)
        .filter(NodeSession.id == node_session_id)
        .first()
    )
    if ns is None:
        logger.error("NodeSession %s not found", node_session_id)
        return
    if getattr(ns, "role", "execution") != "planning":
        return  # evaluator handles execution sessions

    logger.info("Planning observed: ns=%s", node_session_id)

    # Find the associated plan via the worktree-based lookup
    from backend.db.queries import conn as db_conn
    with db_conn() as dbc:
        row = dbc.execute(
            """SELECT plan_id, project_id, planning_worktree, planning_attempts
               FROM plans WHERE planning_worktree = %s""",
            (ns.worktree,),
        ).fetchone()
    if row is None:
        logger.error("No plan found for worktree %s", ns.worktree)
        return
    plan_id = row["plan_id"]
    project_id = row.get("project_id", "default")
    worktree = row["planning_worktree"]
    attempts = row["planning_attempts"] or 0

    dec = None  # set by gate below; needed in FAIL path (Item 3.c)
    # 1. Deterministic assembly
    dag_dict, errs = assemble_plan(worktree)
    if not errs:
        # 2. Pydantic + roster validation
        roster_ids = _get_roster_ids()
        dag, errs = validate_assembled(dag_dict, roster_ids)

    if not errs:
        # 3. Capability selector (existing)
        from backend.planning.capability.selector import resolve_dag_capabilities
        dag_list = [n.model_dump() for n in dag.nodes]
        need_resolve = [n for n in dag_list if not (n.get("capabilities") or [])]
        if need_resolve:
            resolve_dag_capabilities(need_resolve)

        # 4. Check boundary validation (deterministic, replaces LLM check-gen)
        from contracts.plan_assembler import validate_check_boundaries
        errs = validate_check_boundaries(dag_list)

    if not errs:
        # 5. Plan-evaluator gate (existing)
        from backend.evaluator.plan_evaluator import gate_plan
        mg_goal = _get_plan_goal(plan_id)
        dec = gate_plan(dag_list, plan_goal=mg_goal)
        logger.info(
            "Gate decision for %s (attempt %d): action=%s score=%s feedback=%.200s",
            plan_id, attempts + 1,
            dec.action, getattr(dec, "plan_goal_review", "N/A"),
            (dec.feedback_text or "none").replace("\n", " "),
        )

        # [Item 3.b] Persist gate results to node_session
        from backend.db.queries import conn as db_conn
        gate_outcome = "pass" if dec.action == "ratify" else "fail"
        with db_conn() as dbc:
            dbc.execute(
                """UPDATE node_sessions
                      SET gate_outcome = %s,
                          l2_score = %s,
                          feedback = %s::jsonb,
                          l2_feedback = %s::jsonb
                    WHERE id = %s""",
                (
                    gate_outcome,
                    dec.plan_goal_review,
                    json.dumps({"feedback_text": dec.feedback_text}),
                    json.dumps(dec.l2_judgments),
                    ns.id,
                ),
            )

        if dec.action == "ratify":
            # Persist the DAG
            _persist_harness_dag(plan_id, dag_list)
            planning_run_id = f"plan_{plan_id}"
            with db_conn() as dbc:
                dbc.execute(
                    "UPDATE plans SET planning_status = 'gated_ok' WHERE plan_id = %s",
                    (plan_id,),
                )
                dbc.execute(
                    "UPDATE runs SET state = 'done' WHERE id = %s AND state = 'planning'",
                    (planning_run_id,),
                )
            logger.info("Plan %s ratified via harness (run %s done)", plan_id, planning_run_id)
            return

        errs = [dec.feedback_text]
    elif errs:
        logger.warning("Assembly/validation errors for %s: %s", plan_id, errs[:3])

    # FAIL path — retry or give up
    if errs and dec:
        # [Item 3.c] Persist plan gate results on failure too (mid-retry observability)
        from backend.planning.store import update_plan_gate_result
        update_plan_gate_result(
            plan_id=plan_id or "",
            plan_goal_review=dec.plan_goal_review,
            l2_judgments=dec.l2_judgments or [],
            hard_failures=dec.hard_failures or [],
            raw_response=dec.raw_response,
        )

    if attempts >= MAX_PLANNING_ATTEMPTS:
        on_planning_failed(worktree, project_id, "/opt/aipc/conductor/workspace")
        planning_run_id = f"plan_{plan_id}"
        with db_conn() as dbc:
            dbc.execute(
                "UPDATE plans SET planning_status = 'failed', planning_worktree = NULL WHERE plan_id = %s",
                (plan_id,),
            )
            dbc.execute(
                "UPDATE runs SET state = 'failed' WHERE id = %s AND state = 'planning'",
                (planning_run_id,),
            )
        logger.warning("Planning failed for %s after %d attempts (run %s failed)", plan_id, attempts, planning_run_id)
        from shared.outbox import emit as outbox_emit
        outbox_emit(session, RunFailed(
            run_id=None, reason=f"planning failed: {errs[:3]}", ts=time.time(),
        ))
    else:
        # Re-spawn via LangGraph with feedback
        _resume_langgraph_with_feedback(plan_id, errs, dag_dict)


def _get_roster_ids() -> list[str]:
    from backend.db.queries import conn as db_conn
    with db_conn() as dbc:
        rows = dbc.execute(
            "SELECT agent_config_id FROM agent_configs WHERE active = true"
        ).fetchall()
    return [r["agent_config_id"] for r in rows]


def _get_plan_goal(plan_id: str) -> str:
    from backend.db.queries import conn as db_conn
    with db_conn() as dbc:
        row = dbc.execute(
            "SELECT goal FROM plans WHERE plan_id = %s", (plan_id,)
        ).fetchone()
    return row["goal"] if row else ""


def _persist_harness_dag(plan_id: str, dag_list: list[dict]) -> None:
    import json
    from backend.db.queries import conn as db_conn
    with db_conn() as dbc:
        dbc.execute(
            "UPDATE plans SET dag = %s WHERE plan_id = %s",
            (json.dumps(dag_list), plan_id),
        )


def _get_meta_goal(plan_id: str) -> dict:
    import json as _json
    from backend.db.queries import conn as db_conn
    with db_conn() as dbc:
        row = dbc.execute(
            "SELECT goal, partial_meta_goal FROM plans WHERE plan_id = %s", (plan_id,)
        ).fetchone()
    if row and row.get("partial_meta_goal"):
        pmg = row["partial_meta_goal"]
        if isinstance(pmg, str):
            pmg = _json.loads(pmg)
        pmg.setdefault("goal", "")
        pmg.setdefault("spec", "")
        pmg.setdefault("quality_intent", "")
        pmg.setdefault("domain", "general")
        return pmg
    goal = row["goal"] if row else ""
    return {"goal": goal, "spec": "", "quality_intent": "", "domain": "general"}


def _resume_langgraph_with_feedback(
    plan_id: str, feedback: list[str], prior_dag: dict | None,
) -> None:
    """Re-invoke the planner LangGraph with gate_feedback for a retry."""
    feedback_text = "; ".join(feedback)
    meta_goal = _get_meta_goal(plan_id)
    from backend.db.queries import conn as db_conn
    with db_conn() as dbc:
        row = dbc.execute(
            "SELECT project_id FROM plans WHERE plan_id = %s", (plan_id,)
        ).fetchone()
    project_id = row["project_id"] if row else "default"
    state = planner_graph.invoke(
        input={
            "raw_input": "",
            "origin": "system",
            "meta_goal": meta_goal,
            "dag": prior_dag.get("nodes") if prior_dag else None,
            "plan_goal_review": None,
            "clarify_rounds": 0,
            "revise_rounds": 0,
            "status": "formulated",
            "error": None,
            "gate_feedback": feedback_text,
            "planning_session": None,
            "plan_id": plan_id,
            "project_id": project_id,
        },
        config={"configurable": {"thread_id": plan_id}},
    )
    logger.info("LangGraph resumed for plan %s (status=%s)", plan_id, state.get("status"))


# ── Lifespan ─────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: init DB, declare Rabbit topology, start consumers + relay."""
    init_db(cfg)
    bus.declare()

    # Single dispatcher on planner.q (matching evaluator pattern)
    # Multiple consumers on one queue round-robin — dispatcher avoids this.
    def _dispatch(s, payload):
        if "node_session_id" in payload:
            _on_node_observed_planning(s, payload)
        elif payload.get("event_type") == "run.completed" or "run_id" in payload:
            _handle_run_completed(s, payload)
        elif payload.get("event_type") == "run.failed":
            _handle_run_failed(s, payload)
        else:
            logger.warning("No planner handler for payload keys: %s", list(payload.keys()))

    bus.start_consumer("planner.q", _dispatch, "planner.dispatch")
    relay_t = threading.Thread(target=bus.relay_loop, daemon=True)
    relay_t.start()
    consumer_t = threading.Thread(target=bus.start_consuming, daemon=True)
    consumer_t.start()
    logger.info("planner-svc ready")
    yield
    bus.close()


# ── FastAPI app ──────────────────────────────────────────────────────────

app = FastAPI(title="planner-svc", version="0.1.0", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok", "service": "planner"}


# ── BYO-DAG helpers ─────────────────────────────────────────────────


def _validate_supplied_dag_dict(nodes: list[dict]) -> None:
    """Validate a pre-decomposed DAG provided via ``nodes`` (BYO-DAG path).

    Checks: unique IDs, at least one member per node with backend,
    all dependencies resolve within the DAG, graph is acyclic.
    Raises ``ValueError`` on the first failure.
    """
    node_ids: set[str] = set()
    for i, n in enumerate(nodes):
        nid = n.get("id") or n.get("node_id") or f"node-{i + 1}"
        if nid in node_ids:
            raise ValueError(f"Duplicate node id: {nid}")
        node_ids.add(nid)

        members = n.get("members") or []
        if not members:
            raise ValueError(f"Node {nid}: must have at least one member with backend")
        for m in members:
            if not m.get("backend"):
                raise ValueError(
                    f"Node {nid}: member {m.get('agent_config', '?')} missing backend"
                )

    def _nid(n: dict, idx: int) -> str:
        return n.get("id") or n.get("node_id") or f"node-{idx + 1}"

    all_ids = {_nid(n, i) for i, n in enumerate(nodes)}

    # Dependencies must reference existing nodes
    for i, n in enumerate(nodes):
        nid = _nid(n, i)
        for dep in n.get("depends_on") or []:
            if dep not in all_ids:
                raise ValueError(f"Node {nid}: depends_on '{dep}' not found in DAG")

    # Acyclicity check via DFS
    adj: dict[str, list[str]] = {nid: [] for nid in all_ids}
    for i, n in enumerate(nodes):
        nid = _nid(n, i)
        for dep in n.get("depends_on") or []:
            adj.setdefault(dep, []).append(nid)

    visited: set[str] = set()
    stack: set[str] = set()

    def _dfs(nid: str) -> None:
        if nid in stack:
            raise ValueError(f"Cycle detected in DAG involving node {nid}")
        if nid in visited:
            return
        visited.add(nid)
        stack.add(nid)
        for neighbor in adj.get(nid, []):
            _dfs(neighbor)
        stack.remove(nid)

    for nid in all_ids:
        if nid not in visited:
            _dfs(nid)


def _handle_byo_dag(body: GoalRequest, nodes: list[dict]) -> dict:
    """Process a BYO-DAG request: validate, generate checks, gate, persist."""
    from backend.evaluator.schema import Check as CheckSchema
    from backend.planning.store import save_plan
    from backend.planning.capability.checkgen import generate_capability_checks
    from backend.evaluator.plan_evaluator import gate_plan
    from shared.schema import (
        PlanNode as SharedPlanNode,
        TaskSpec, NodeSuccess, NodeMember,
        SuccessCriterion, Plan as SharedPlan,
    )

    plan_id = f"plan_{uuid4().hex[:8]}"

    # 1. Validate the supplied DAG
    _validate_supplied_dag_dict(nodes)

    # 2. Generate checks for nodes that don't already have them
    for n in nodes:
        if not (n.get("checks") or []):
            n["checks"] = generate_capability_checks(n)

    # 3. Normalise node_id → id for gate_plan compatibility
    for n in nodes:
        if "id" not in n and "node_id" in n:
            n["id"] = n["node_id"]

    # 4. Run plan-evaluator gate (L1 structural + L2 rubric)
    dec = gate_plan(nodes, plan_goal=body.raw_input)

    # 5. Convert to SharedPlanNode and persist
    dag_nodes: list[SharedPlanNode] = []
    for n in nodes:
        members = [NodeMember(**m) for m in (n.get("members") or [])]
        t = n.get("task") or {}
        task_obj = TaskSpec(
            text=t.get("text", "") if isinstance(t, dict) else str(t),
            inputs=t.get("inputs", []) if isinstance(t, dict) else [],
            deliverables=t.get("deliverables", []) if isinstance(t, dict) else [],
        )
        s = n.get("success") or {}
        success_obj = NodeSuccess(
            text=s.get("text", "") if isinstance(s, dict) else str(s),
        )
        checks = [
            CheckSchema(**c) if isinstance(c, dict) else c
            for c in (n.get("checks") or [])
        ]
        dag_nodes.append(SharedPlanNode(
            id=n.get("id", f"node-{len(dag_nodes) + 1}"),
            members=members,
            depends_on=n.get("depends_on") or [],
            task=task_obj,
            success=success_obj,
            checks=checks,
            capabilities=n.get("capabilities", []),
            project_id=n.get("project_id", body.project_id),
        ))

    save_plan(plan=SharedPlan(
        plan_id=plan_id,
        project_id=body.project_id,
        user_intent=body.raw_input,
        goal=body.raw_input,
        success=SuccessCriterion(text=""),
        dag=dag_nodes,
        version=1,
        needs_usage_sim=False,
    ))

    return {
        "status": "gated_ok" if dec.action == "ratify" else "formulated",
        "plan_id": plan_id,
        "plan_goal_review": dec.plan_goal_review,
        "error": None if dec.action == "ratify" else dec.feedback_text,
    }


@app.post("/goal")
def submit_goal(body: GoalRequest):
    """Submit a raw goal for planning.

    Supports both LLM-formulated goals (default) and BYO-DAG via
    ``nodes``.  Plans are persisted for all statuses — when the graph
    detects insufficient specificity the plan is stored as
    ``awaiting_clarification`` so the human can answer via
    ``/clarify/{plan_id}``.
    """
    # ── BYO-DAG path: skip LangGraph entirely when nodes are supplied ──
    if body.nodes:
        return _handle_byo_dag(body, body.nodes)

    from backend.planning.store import save_plan, get_active_run_for_project

    # Reject if project already has a non-terminal run
    active = get_active_run_for_project(body.project_id or "default")
    if active:
        return JSONResponse(
            status_code=409,
            content={
                "error": f"Project {body.project_id} already has an active run "
                         f"({active['state']}): {active['id']}. "
                         "Complete or cancel it before starting a new goal.",
            },
        )

    from backend.planning.schema import (
        Plan, PlanNode, TaskSpec, NodeSuccess, SuccessCriterion, NodeMember,
    )
    from backend.evaluator.schema import Check

    thread_id = body.project_id or f"goal_{uuid4().hex[:12]}"
    state = planner_graph.invoke(
        input={
            "raw_input": body.raw_input,
            "origin": body.origin,
            "meta_goal": None,
            "dag": None,
            "plan_goal_review": None,
            "clarify_rounds": 0,
            "revise_rounds": 0,
            "status": "new",
            "error": None,
            "gate_feedback": None,
            "project_id": body.project_id,
        },
        config={"configurable": {"thread_id": thread_id}},
    )

    plan_id = f"plan_{uuid4().hex[:8]}"

    if state["status"] == "awaiting_clarification":
        # Minimal plan row so formulate_or_clarify can find it
        save_plan(plan=Plan(
            plan_id=plan_id,
            project_id=body.project_id,
            user_intent=body.raw_input,
            goal=body.raw_input,
            success=SuccessCriterion(text=""),
            version=1,
        ))
        return {
            "status": "awaiting_clarification",
            "plan_id": plan_id,
            "meta_goal": state.get("meta_goal"),
        }

    # generating — plan is being built asynchronously by meta-planner
    if state["status"] == "generating":
        plan_id = state.get("plan_id") or f"plan_{uuid4().hex[:8]}"
        from backend.planning.store import save_plan
        from backend.planning.schema import Plan, SuccessCriterion
        mg = state.get("meta_goal") or {}
        save_plan(plan=Plan(
            plan_id=plan_id,
            project_id=body.project_id,
            user_intent=body.raw_input,
            goal=mg.get("goal", body.raw_input) if isinstance(mg, dict) else body.raw_input,
            success=SuccessCriterion(text=""),
            version=1,
        ))
        planning_session = state.get("planning_session")
        wt = None
        if planning_session:
            from backend.db.queries import conn as db_conn
            with db_conn() as dbc:
                row = dbc.execute(
                    "SELECT worktree FROM node_sessions WHERE id = %s",
                    (planning_session,),
                ).fetchone()
            if row:
                wt = row["worktree"]
        return {
            "status": "generating",
            "plan_id": plan_id,
            "planning_session": planning_session,
            "worktree": wt,
            "meta_goal": state.get("meta_goal"),
        }

    # gated_ok — persist full plan with DAG via shared Pydantic schema
    from shared.schema import PlanNode as SharedPlanNode, TaskSpec, NodeSuccess
    from shared.schema import NodeMember, SuccessCriterion, Plan as SharedPlan
    from backend.evaluator.schema import Check as CheckSchema

    dag_dict_list = state.get("dag", []) or []
    if isinstance(dag_dict_list, dict):
        dag_dict_list = dag_dict_list.get("nodes", [dag_dict_list])

    dag_nodes: list[SharedPlanNode] = []
    for n in dag_dict_list:
        members = [NodeMember(**m) for m in (n.get("members") or [])]
        t = n.get("task") or {}
        task_obj = TaskSpec(
            text=t.get("text", "") if isinstance(t, dict) else str(t),
            inputs=t.get("inputs", []) if isinstance(t, dict) else [],
            deliverables=t.get("deliverables", []) if isinstance(t, dict) else [],
        )
        s = n.get("success") or {}
        success_obj = NodeSuccess(
            text=s.get("text", "") if isinstance(s, dict) else str(s),
        )
        checks = [
            CheckSchema(**c) if isinstance(c, dict) else c
            for c in (n.get("checks") or [])
        ]
        dag_nodes.append(SharedPlanNode(
            id=n.get("id", f"node-{len(dag_nodes) + 1}"),
            members=members,
            depends_on=n.get("depends_on") or [],
            task=task_obj,
            success=success_obj,
            checks=checks,
            capabilities=n.get("capabilities", []),
            project_id=n.get("project_id", body.project_id),
        ))

    mg = state.get("meta_goal") or {}
    needs_usage_sim = mg.get("needs_usage_sim", False) if isinstance(mg, dict) else False
    from backend.planning.store import save_plan
    save_plan(plan=SharedPlan(
        plan_id=plan_id,
        project_id=body.project_id,
        user_intent=body.raw_input,
        goal=mg.get("goal", body.raw_input) if isinstance(mg, dict) else body.raw_input,
        success=SuccessCriterion(text=""),
        dag=dag_nodes,
        version=1,
        needs_usage_sim=needs_usage_sim,
    ))

    return {
        "status": state.get("status"),
        "plan_id": plan_id,
        "plan_goal_review": state.get("plan_goal_review"),
        "error": state.get("error"),
    }


@app.post("/clarify/{plan_id}")
def answer_clarification(plan_id: str, body: ClarifyRequest):
    """Resume a plan that paused for clarification.

    Folds the human answer into the stored ``clarify_context`` and
    re-formulates.  When the plan is specific enough, continues the
    graph through inject → decompose → select_capabilities →
    generate_checks → gate so the full DAG is ready for ratification.
    """
    from backend.planning.meta_planner.clarify import formulate_or_clarify

    result = formulate_or_clarify(plan_id, new_answer=body.answer)

    from backend.planning.meta_planner.goal_formulator import MetaGoal
    if not isinstance(result, MetaGoal):
        from backend.planning.meta_planner.clarify import ClarifyPending
        if isinstance(result, ClarifyPending):
            return {
                "status": "awaiting_clarification",
                "plan_id": plan_id,
                "questions": result.questions,
                "reason": result.reason,
            }
        return {"status": "error", "plan_id": plan_id, "error": "unexpected result type"}

    # ── MetaGoal resolved — continue the graph through decompose → gate ──
    from backend.planning.store import get_plan
    plan_dict = get_plan(plan_id)
    project_id = (plan_dict or {}).get("project_id", "default")
    raw_input = (plan_dict or {}).get("user_intent", result.goal)

    thread_id = project_id or f"clarify_{uuid4().hex[:12]}"
    state = planner_graph.invoke(
        input={
            "raw_input": raw_input,
            "origin": "human",
            "meta_goal": result.model_dump(),
            "dag": None,
            "plan_goal_review": None,
            "clarify_rounds": 0,
            "revise_rounds": 0,
            "status": "formulated",
            "error": None,
            "gate_feedback": None,
            "project_id": project_id,
            "plan_id": plan_id,
        },
        config={"configurable": {"thread_id": thread_id}},
    )

    if state["status"] == "awaiting_clarification":
        # Still not specific enough — this shouldn't happen since MetaGoal
        # is already resolved, but handle gracefully.
        return {
            "status": "awaiting_clarification",
            "plan_id": plan_id,
            "meta_goal": state.get("meta_goal"),
        }

    if state["status"] == "generating":
        # Async meta-planner has been spawned — DAG not ready yet.
        # The async completion will be handled by _on_node_observed_planning.
        return {
            "status": "generating",
            "plan_id": plan_id,
            "plan_goal_review": None,
            "error": None,
        }

    # Persist the full plan with DAG
    from shared.schema import PlanNode as SharedPlanNode, TaskSpec, NodeSuccess
    from shared.schema import NodeMember, SuccessCriterion, Plan as SharedPlan
    from backend.evaluator.schema import Check as CheckSchema

    dag_dict_list = state.get("dag", []) or []
    if isinstance(dag_dict_list, dict):
        dag_dict_list = dag_dict_list.get("nodes", [dag_dict_list])

    dag_nodes: list[SharedPlanNode] = []
    for n in dag_dict_list:
        members = [NodeMember(**m) for m in (n.get("members") or [])]
        t = n.get("task") or {}
        task_obj = TaskSpec(
            text=t.get("text", "") if isinstance(t, dict) else str(t),
            inputs=t.get("inputs", []) if isinstance(t, dict) else [],
            deliverables=t.get("deliverables", []) if isinstance(t, dict) else [],
        )
        s = n.get("success") or {}
        success_obj = NodeSuccess(
            text=s.get("text", "") if isinstance(s, dict) else str(s),
        )
        checks = [
            CheckSchema(**c) if isinstance(c, dict) else c
            for c in (n.get("checks") or [])
        ]
        dag_nodes.append(SharedPlanNode(
            id=n.get("id", f"node-{len(dag_nodes) + 1}"),
            members=members,
            depends_on=n.get("depends_on") or [],
            task=task_obj,
            success=success_obj,
            checks=checks,
            capabilities=n.get("capabilities", []),
            project_id=project_id,
        ))

    mg = state.get("meta_goal") or {}
    needs_usage_sim = mg.get("needs_usage_sim", False) if isinstance(mg, dict) else False
    from backend.planning.store import save_plan
    save_plan(plan=SharedPlan(
        plan_id=plan_id,
        project_id=project_id,
        user_intent=raw_input,
        goal=mg.get("goal", raw_input) if isinstance(mg, dict) else raw_input,
        success=SuccessCriterion(text=""),
        dag=dag_nodes,
        version=1,
        needs_usage_sim=needs_usage_sim,
    ))

    return {
        "status": state.get("status"),
        "plan_id": plan_id,
        "plan_goal_review": state.get("plan_goal_review"),
        "error": state.get("error"),
    }


@app.post("/ratify/{plan_id}", response_model=RatifyResponse)
def ratify_plan(plan_id: str):
    """Human ratify — runs plan gate, persists, emits ``PlanRatified``.

    Requires the plan to pass L1 structural + L2 rubric evaluation
    (``run_plan_gate``).  Returns gate feedback on failure.
    """
    from shared.outbox import emit

    from backend.planning.store import get_plan, update_plan_gate_result, set_ratified, get_active_run_for_project
    from backend.evaluator.plan_evaluator import run_plan_gate

    plan_dict = get_plan(plan_id)
    if not plan_dict:
        return RatifyResponse(status="not_found", plan_id=plan_id)

    # run_plan_gate operates on the dict directly
    gate_result = run_plan_gate(plan_dict)
    if gate_result.action != "ratify":
        return RatifyResponse(
            status="gate_failed",
            plan_id=plan_id,
            gate_feedback=gate_result.feedback_text,
            plan_goal_review=gate_result.plan_goal_review,
        )

    # Reject if project already has an active (non-terminal) run
    project_id = plan_dict.get("project_id", "default")
    active = get_active_run_for_project(project_id)
    if active:
        return JSONResponse(
            status_code=409,
            content={
                "error": f"Project {project_id} already has an active run "
                         f"({active['state']}): {active['id']}. "
                         "Complete or cancel it before ratifying a new plan.",
            },
        )

    # Persist ratification + gate score
    set_ratified(plan_id)
    if gate_result.plan_goal_review is not None:
        update_plan_gate_result(
            plan_id=plan_id,
            plan_goal_review=gate_result.plan_goal_review,
            l2_judgments=gate_result.l2_judgments or [],
            hard_failures=[],
            raw_response=gate_result.raw_response,
        )

    run_id = f"run_{uuid4().hex[:8]}"
    from backend.planning.store import save_run
    save_run({"id": run_id, "plan_id": plan_id, "project_id": project_id, "state": "created"})
    from shared.db import session as db_session
    with db_session() as s:
        emit(s, PlanRatified(
            plan_id=plan_id,
            run_id=run_id,
            project_id=plan_dict.get("project_id", "default"),
            env=cfg.env,
            ts=time.time(),
        ))
        s.commit()

    return RatifyResponse(
        status="ratified",
        plan_id=plan_id,
        run_id=run_id,
        plan_goal_review=gate_result.plan_goal_review,
    )


# ── Main ─────────────────────────────────────────────────────────────────


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PLANNER_PORT", "8094"))
    uvicorn.run(app, host="0.0.0.0", port=port)
