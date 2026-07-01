"""planner-svc entrypoint.

FastAPI endpoints for goal submission, clarification, and ratification.
Background consumer for run.completed/run.failed + outbox relay.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI
from pydantic import BaseModel

from contracts.events import PlanRatified, RunCompleted, RunFailed
from shared.bus import EventBus
from shared.config import ServiceConfig
from shared.db import init_db

from services.planner.graph import build_planner_graph

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


# ── Lifespan ─────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: init DB, declare Rabbit topology, start consumers + relay."""
    init_db(cfg)
    bus.declare()
    bus.start_consumer("planner.q", _handle_run_completed, "planner-svc")
    bus.start_consumer("planner.q", _handle_run_failed, "planner-svc")
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


@app.post("/goal")
def submit_goal(body: GoalRequest):
    """Submit a raw goal for planning.

    Supports both LLM-formulated goals (default) and BYO-DAG via
    ``nodes``.  Plans are persisted for all statuses — when the graph
    detects insufficient specificity the plan is stored as
    ``awaiting_clarification`` so the human can answer via
    ``/clarify/{plan_id}``.
    """
    from backend.planning.schema import (
        Plan, PlanNode, TaskSpec, NodeSuccess, SuccessCriterion, NodeMember,
    )
    from backend.evaluator.schema import Check
    from backend.planning.store import save_plan

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
    from backend.planning.store import save_plan
    save_plan(plan=SharedPlan(
        plan_id=plan_id,
        project_id=body.project_id,
        user_intent=body.raw_input,
        goal=mg.get("goal", body.raw_input) if isinstance(mg, dict) else body.raw_input,
        success=SuccessCriterion(text=""),
        dag=dag_nodes,
        version=1,
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
    from backend.planning.store import save_plan
    save_plan(plan=SharedPlan(
        plan_id=plan_id,
        project_id=project_id,
        user_intent=raw_input,
        goal=mg.get("goal", raw_input) if isinstance(mg, dict) else raw_input,
        success=SuccessCriterion(text=""),
        dag=dag_nodes,
        version=1,
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

    from backend.planning.store import get_plan, update_plan_gate_result, set_ratified
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

    # Persist ratification + gate score
    set_ratified(plan_id)
    if gate_result.plan_goal_review is not None:
        update_plan_gate_result(
            plan_id=plan_id,
            plan_goal_review=gate_result.plan_goal_review,
            l2_judgments=gate_result.l2_judgments or [],
            hard_failures=[],
        )

    run_id = f"run_{uuid4().hex[:8]}"
    from backend.planning.store import save_run
    save_run({"id": run_id, "plan_id": plan_id, "state": "created"})
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
