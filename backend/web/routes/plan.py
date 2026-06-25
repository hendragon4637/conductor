"""Plan routes — propose, ratify, and manage runs (execution instances)."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import uuid
from typing import Any, Optional

import psycopg
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.planning.schema import Plan, PlanNode
from backend.planning.store import get_plan as load_persisted_plan, save_plan as persist_plan
from backend.planning.store import save_run, get_run, list_runs, update_run_state, get_node_sessions
from backend.planning.meta_planner import decompose, generate_checks, attach_checks_to_dag, formulate
from backend.planning.meta_planner.goal_formulator import MetaGoal, enrich_with_conventions
from backend.planning.meta_planner.clarify import ClarifyPending, formulate_or_clarify
from backend.planning.meta_planner.split import split_oversized

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/plans", tags=["plans"])

_plans: dict[str, dict[str, Any]] = {}


def _title_from_intent(user_intent: str | None, plan_id: str) -> str:
    if user_intent:
        return user_intent[:60]
    return plan_id


def _ui_nodes_from_dag(dag: list[dict[str, Any]]) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    for index, node in enumerate(dag, start=1):
        description = node.get("task") or node.get("description") or ""
        title = node.get("title") or description[:40] or f"node-{index}"
        nodes.append({
            "node_id": node.get("id") or node.get("node_id") or f"node-{index}",
            "title": title,
            "description": description,
            "depends_on": node.get("depends_on", []),
            "status": node.get("status", "pending"),
            "members": node.get("members", []),
            "agent_config_id": node.get("agent_config"),
            "success_criterion": node.get("success") or node.get("success_criterion"),
            "backend": node.get("backend"),
        })
    return nodes


def _ui_plan_from_db_row(row: dict[str, Any]) -> dict[str, Any]:
    user_intent = row.get("user_intent")
    created_at = row.get("created_at")
    plan_status = row.get("plan_status", "draft")
    clarify_ctx = row.get("clarify_context") or []
    clarify_questions = []
    if clarify_ctx and isinstance(clarify_ctx, list):
        for r in clarify_ctx:
            if r.get("answers") is None:
                clarify_questions.extend(r.get("questions", []))
    return {
        "plan_id": row["plan_id"],
        "title": _title_from_intent(user_intent, row["plan_id"]),
        "description": user_intent,
        "worktree_id": None,
        "project_id": row.get("project_id"),
        "ratified": row.get("ratified", False),
        "version": row.get("version", 1),
        "plan_status": plan_status,
        "nodes": _ui_nodes_from_dag(row.get("dag") or []),
        "created_at": created_at.isoformat() if created_at else None,
        "clarify_questions": clarify_questions if plan_status == "awaiting_clarification" else [],
        "_source": "db",
    }


def _get_or_load_plan(plan_id: str) -> dict[str, Any] | None:
    existing = _plans.get(plan_id)
    if existing:
        return existing
    row = load_persisted_plan(plan_id)
    if not row:
        return None
    plan = _ui_plan_from_db_row(row)
    _plans[plan_id] = plan
    return plan


class NodeMemberSpec(BaseModel):
    """Per-member spec within a node."""
    agent_config: str
    backend: str = "opencode"
    role: str = "executor"


class NodeTaskSpec(BaseModel):
    """Node-scoped task."""
    text: str = ""
    inputs: list[str] = []
    deliverables: list[str] = []


class NodeSuccessSpec(BaseModel):
    """Prose-only node success."""
    text: str = ""


class NodeSpec(BaseModel):
    """Canonical node spec for BYO-DAG (E2E spec Part 2).

    When provided to create_plan, the brain is skipped;
    the DAG is validated (per-member backend, deps resolve, acyclic),
    checks are still generated, and ratification is still required.
    """
    id: str = ""
    title: str = ""
    description: str = ""
    backend: str = "opencode"
    members: list[NodeMemberSpec] = []
    depends_on: list[str] = []
    agent_config_id: Optional[str] = None
    task: NodeTaskSpec = NodeTaskSpec()
    success: NodeSuccessSpec = NodeSuccessSpec()
    success_criterion: Optional[str] = None  # legacy compat


class PlanPropose(BaseModel):
    """Dual-input plan creation (File 04).

    - ``goal`` + ``spec`` → plan brain decomposition.
    - ``quality_intent`` → evaluator check grounding.
    - ``nodes`` (optional) → BYO-DAG: skip brain, validate supplied DAG,
      still generate checks + plan.success + ratify.
    """
    title: str = ""
    goal: str = ""
    spec: Optional[str] = None
    quality_intent: Optional[str] = None
    backend_type: Optional[str] = None
    worktree_id: Optional[str] = None
    project_id: Optional[str] = None
    plan_id: Optional[str] = None
    nodes: Optional[list[NodeSpec]] = None
    description: Optional[str] = None
    use_meta_planner: bool = False


class RatifyRequest(BaseModel):
    ratified: bool = True
    comment: Optional[str] = None


class AppendNode(BaseModel):
    title: str
    description: str
    depends_on: Optional[list[str]] = None
    members: Optional[list[str]] = None
    agent_config_id: Optional[str] = None  # legacy compat
    success_criterion: Optional[str] = None


class RefineRequest(BaseModel):
    instruction: str
    image_data: Optional[str] = None


class CreateRunRequest(BaseModel):
    note: Optional[str] = None


# ── Plan endpoints ────────────────────────────────────────────────

@router.get("")
async def list_plans():
    """Return all plans — in-memory editing buffer + DB-backed drafts."""
    from backend.db import queries as db_q
    in_mem = list(_plans.values())
    try:
        with db_q.conn() as c, c.cursor() as cur:
            cur.execute(
                "SELECT plan_id, project_id, user_intent, "
                "       ratified, version, created_at "
                "FROM plans ORDER BY created_at DESC"
            )
            db_plans = [_ui_plan_from_db_row(row) for row in cur.fetchall()]
    except Exception:
        db_plans = []
    seen = {p["plan_id"] for p in in_mem}
    return in_mem + [p for p in db_plans if p["plan_id"] not in seen]


@router.post("")
async def propose_plan(req: PlanPropose):
    pid = req.plan_id or f"plan-{len(_plans) + 1}"
    safe_pid = re.sub(r"[^a-z0-9-]", "-", pid.lower()).strip("-") or "default"
    project_id = req.project_id or f"proj-{safe_pid}"
    now = __import__("datetime").datetime.now().isoformat()
    goal = req.goal or req.description or req.title or ""
    user_intent = goal

    nodes: list[dict[str, Any]] = []
    if req.nodes:
        # BYO-DAG path: validate supplied nodes, skip brain (File 04.1b)
        _validate_supplied_dag(req.nodes)
        for i, n in enumerate(req.nodes):
            members_raw = []
            for m in n.members:
                members_raw.append({
                    "agent_config": m.agent_config,
                    "backend": m.backend,
                    "role": m.role,
                })
            task_text = n.task.text or n.description or n.title or ""
            success_text = n.success.text or n.success_criterion or "Complete the task"
            nodes.append({
                "node_id": n.id or f"node-{i + 1}",
                "title": n.title or task_text[:40],
                "description": task_text,
                "depends_on": n.depends_on or [],
                "backend": n.backend,
                "members": members_raw,
                "agent_config_id": n.agent_config_id or (
                    members_raw[0]["agent_config"] if members_raw else "opencode:backend-executor"
                ),
                "task": {"text": task_text, "inputs": n.task.inputs, "deliverables": n.task.deliverables},
                "success": {"text": success_text},
                "success_criterion": success_text,
            })
    elif req.use_meta_planner:
        try:
            # File 06: First check if clarification is needed
            mg = formulate(raw_input=goal, origin="internal_drive")
            if mg.needs_clarification:
                # Persist plan in awaiting_clarification state
                clarify_ctx = json.dumps([{
                    "round": 1,
                    "questions": mg.questions,
                    "answers": None,
                }])
                _persist_plan_clarification(
                    pid=pid, project_id=project_id, goal=goal,
                    user_intent=goal, plan_status="awaiting_clarification",
                    clarify_context=clarify_ctx, clarify_rounds=1,
                    partial_meta_goal=mg.model_dump_json(),
                )
                plan = {
                    "plan_id": pid, "title": req.title or goal[:60],
                    "description": req.description or goal, "goal": goal,
                    "project_id": project_id, "ratified": False,
                    "nodes": [], "created_at": now,
                    "plan_status": "awaiting_clarification",
                    "clarify_questions": mg.questions,
                    "clarify_round": 1,
                }
                _plans[pid] = plan
                return plan

            # Proceed with full meta-planner pipeline (decompose → split → checks)
            mp_nodes = _generate_via_meta_planner(
                goal=goal,
                spec=req.spec or "",
                quality_intent=req.quality_intent or "",
                plan_id=pid,
                meta_goal=mg,
            )
            nodes = mp_nodes
            # Override goal/spec with meta-planner output
            if mp_nodes and "meta_goal" in mp_nodes[0]:
                mg_dict = mp_nodes[0].pop("meta_goal", {})
                goal = mg_dict.get("goal", goal)
                req.spec = mg_dict.get("spec", req.spec)
                req.quality_intent = mg_dict.get("quality_intent", req.quality_intent)
        except Exception as exc:
            logger.warning("Meta-planner failed, falling back: %s", exc)
            if req.spec and req.quality_intent:
                nodes = _generate_nodes_from_intent(req, pid, project_id)
    elif req.spec and req.quality_intent:
        nodes = _generate_nodes_from_intent(req, pid, project_id)

    plan = {
        "plan_id": pid,
        "title": req.title or goal[:60],
        "description": req.description or goal,
        "goal": goal,
        "spec": req.spec,
        "quality_intent": req.quality_intent,
        "backend_type": req.backend_type,
        "worktree_id": req.worktree_id,
        "project_id": project_id,
        "ratified": False,
        "nodes": nodes,
        "created_at": now,
    }
    if nodes:
        plan["plan_status"] = "formulated"
    else:
        plan["plan_status"] = "draft"
    _plans[pid] = plan
    return plan


@router.get("/{plan_id}")
async def get_plan(plan_id: str):
    plan = _get_or_load_plan(plan_id)
    if not plan:
        raise HTTPException(404, "Plan not found")
    return plan


@router.post("/{plan_id}/ratify")
async def ratify_plan(plan_id: str, req: RatifyRequest):
    """Ratify a plan — human approves the checks/spec. Idempotent.

    Does NOT spawn execution. Execution is triggered via create_run + approve_run.
    """
    plan_data = _get_or_load_plan(plan_id)
    if not plan_data:
        raise HTTPException(404, "Plan not found")

    if req.ratified:
        from backend.evaluator.plan_evaluator import run_plan_gate
        decision = run_plan_gate(plan_data)
        if decision.action == "revise":
            raise HTTPException(
                400,
                detail={
                    "error": "Plan gate rejected",
                    "reason": decision.reason,
                    "feedback": decision.feedback_text,
                    "gate_exhausted": plan_data.get("gate_exhausted", False),
                },
            )
        plan_data["plan_goal_review"] = decision.plan_goal_review
        plan_data["plan_l2_judgments"] = decision.l2_judgments
        plan_data["plan_l2_hard_failures"] = decision.hard_failures
    else:
        decision = None

    plan_data["ratified"] = req.ratified
    if req.comment:
        plan_data["comment"] = req.comment

    if req.ratified:
        if decision is None:
            raise HTTPException(500, "Plan gate decision missing")
        from backend.planning.store import set_ratified, update_plan_gate_result
        update_plan_gate_result(
            plan_id,
            decision.plan_goal_review,
            decision.l2_judgments,
            decision.hard_failures,
        )
        set_ratified(plan_id)
        # Persist nodes from in-memory plan to DB
        _persist_plan_dag(plan_data)

    return plan_data


class ClarifyAnswer(BaseModel):
    answer: str


@router.post("/{plan_id}/clarify")
async def answer_clarification(plan_id: str, req: ClarifyAnswer):
    """Answer clarifying questions for a plan awaiting human input.

    File 06: folds the answer into the multi-turn context, re-formulates,
    and if resolved, automatically proceeds to decompose → split → checks.
    """
    plan_data = _get_or_load_plan(plan_id)
    if not plan_data:
        raise HTTPException(404, "Plan not found")

    result = formulate_or_clarify(plan_id, new_answer=req.answer)

    if isinstance(result, ClarifyPending):
        # Still needs more input
        plan_data["plan_status"] = "awaiting_clarification"
        plan_data["clarify_questions"] = result.questions
        plan_data["nodes"] = []
        _plans[plan_id] = plan_data
        return plan_data

    if isinstance(result, MetaGoal):
        # Resolved — run the rest of the meta-planner pipeline
        goal = plan_data.get("goal") or plan_data.get("description") or ""
        spec = plan_data.get("spec") or ""
        quality_intent = plan_data.get("quality_intent") or ""
        try:
            mp_nodes = _generate_via_meta_planner(
                goal=goal,
                spec=spec,
                quality_intent=quality_intent,
                plan_id=plan_id,
                meta_goal=result,
            )
            plan_data["nodes"] = mp_nodes
            if mp_nodes and "meta_goal" in mp_nodes[0]:
                mp_nodes[0].pop("meta_goal", None)
            plan_data["plan_status"] = "formulated"
            plan_data.pop("clarify_questions", None)
        except Exception as exc:
            logger.exception("Meta-planner pipeline failed after clarify")
            plan_data["plan_status"] = "draft"
            plan_data["pipeline_error"] = str(exc)
        _plans[plan_id] = plan_data

    return plan_data


def _generate_nodes_from_intent(
    req: PlanPropose, plan_id: str, project_id: str
) -> list[dict[str, Any]]:
    """Parse quality_intent into node specs.

    Expected format:
      "Node 1: <title> (backend=<name>, class-a|b [, ...])."
    Falls back to a single default node if no nodes parsed.
    """
    intent = req.quality_intent or ""
    parsed: list[dict[str, Any]] = []
    pattern = r"Node\s+(\d+):\s*(.+?)\s*(?:\(([^)]*)\))?\s*\.?"
    for m in re.finditer(pattern, intent, re.IGNORECASE | re.DOTALL):
        title = m.group(2).strip()
        props_str = m.group(3) or ""
        backend = "opencode"
        members: list[str] = []
        for part in props_str.split(","):
            part = part.strip()
            if part.startswith("backend="):
                backend = part.split("=", 1)[1].strip()
            elif part.startswith("members="):
                ms = part.split("=", 1)[1].strip()
                members = [x.strip() for x in ms.split("+")]
        parsed.append({
            "node_id": f"node-{len(parsed) + 1}",
            "title": title,
            "description": title,
            "depends_on": [],
            "backend": backend,
            "members": members,
            "agent_config_id": _agent_config_for_backend(backend, members),
            "success_criterion": "Complete the task",
        })
    if not parsed:
        backend = req.backend_type or "opencode"
        parsed.append({
            "node_id": "node-1",
            "title": req.title,
            "description": req.spec or req.description or req.title,
            "depends_on": [],
            "backend": backend,
            "members": [],
            "agent_config_id": _agent_config_for_backend(backend, []),
            "success_criterion": "Complete the task",
        })
    return parsed


def _generate_via_meta_planner(
    goal: str,
    spec: str,
    quality_intent: str,
    plan_id: str,
    meta_goal: MetaGoal | None = None,
) -> list[dict[str, Any]]:
    """Run the full meta-planner pipeline: formulate → decompose → split → check-gen.

    Args:
        goal: Raw user goal/description.
        spec: Optional spec/constraints text.
        quality_intent: Quality guidance text.
        plan_id: Target plan ID.
        meta_goal: Pre-formulated MetaGoal (skip formulate step). Used by
            the clarify-answer flow (File 06) where formulate was already
            called via formulate_or_clarify().

    Returns:
        List of node dicts compatible with the plan response format.
        Empty list if the goal needs clarification (caller should handle).

    Note:
        Pipeline order (File 07):
          formulate → decompose (with size_estimate) → split_oversized → check-gen
    """
    if meta_goal is None:
        mg = formulate(raw_input=goal, origin="internal_drive")
        if mg.needs_clarification:
            logger.warning("Meta-planner deferred: %s", mg.questions)
            return []
        meta_goal = mg

    # Convention injection (File 02): enrich meta-goal with domain profile
    enriched = enrich_with_conventions(meta_goal, goal)
    if enriched.needs_clarification:
        logger.warning("Convention enrichment deferred: %s", enriched.questions)
        # Still proceed — clarification is advisory for internal_drive
    meta_goal = enriched

    # Stage 2: Decompose into DAG (with size_estimate from File 07)
    dag = decompose(
        goal=meta_goal.goal,
        spec=meta_goal.spec or spec,
        quality_intent=meta_goal.quality_intent or quality_intent,
    )

    if not dag or not dag.nodes:
        logger.warning("Meta-planner produced empty DAG")
        return []

    # Stage 2b: Split oversized nodes at planning time (File 07)
    dag = split_oversized(dag, meta_goal)

    # Stage 3: Generate checks (separate LLM call)
    all_checks = generate_checks(
        dag=dag,
        quality_intent=meta_goal.quality_intent,
    )
    attach_checks_to_dag(dag, all_checks)

    # Convert PlanDAG → node dicts for the route response
    node_dicts = []
    for n in dag.nodes:
        checks_list = []
        for c in getattr(n, "checks", []):
            checks_list.append({
                "id": c.id,
                "type": c.type,
                "criterion": c.criterion,
                "check_cmd": c.check_cmd,
                "rubric_item": c.rubric_item,
                "weight": c.weight,
                "provenance": c.provenance,
            })
        members_list = [
            {"agent_config": m.agent_config, "backend": m.backend, "role": m.role}
            for m in n.members
        ]
        task_text = n.task.text
        success_text = n.success.text
        backend = members_list[0].get("backend", "opencode") if members_list else "opencode"
        node_dicts.append({
            "node_id": n.id,
            "title": task_text[:40],
            "description": task_text,
            "depends_on": n.depends_on,
            "backend": backend,
            "members": members_list,
            "agent_config_id": members_list[0]["agent_config"] if members_list else "opencode:backend-executor",
            "task": {"text": task_text, "inputs": n.task.inputs, "deliverables": n.task.deliverables},
            "success": {"text": success_text},
            "success_criterion": success_text,
            "checks": checks_list,
            "meta_goal": meta_goal.model_dump(),
            "size_estimate": getattr(n, "size_estimate", 0),
            "parent_node_id": getattr(n, "parent_node_id", None),
            "node_status": getattr(n, "node_status", "active"),
        })
    return node_dicts


def _validate_supplied_dag(nodes: list[NodeSpec]) -> None:
    """Validate a pre-decomposed DAG (BYO-DAG path).

    Checks: per-member backend, deps resolve, acyclic.
    Raises ValueError on first failure.
    """
    node_ids: set[str] = set()
    for i, n in enumerate(nodes):
        nid = n.id or f"node-{i + 1}"
        if nid in node_ids:
            raise ValueError(f"Duplicate node id: {nid}")
        node_ids.add(nid)

        if not n.members:
            raise ValueError(f"Node {nid}: must have at least one member with backend")
        for m in n.members:
            if not m.backend:
                raise ValueError(f"Node {nid}: member {m.agent_config} missing backend")

        # Deps must reference existing nodes (or be empty)
        for dep in n.depends_on:
            if dep not in node_ids and dep not in {x.id or f"node-{j+1}" for j, x in enumerate(nodes[:i])}:
                # Accept forward references (deps to nodes defined later) but validate at the end
                pass

    # Final pass: all deps must resolve
    all_ids = {
        n.id or f"node-{i + 1}"
        for i, n in enumerate(nodes)
    }
    for i, n in enumerate(nodes):
        nid = n.id or f"node-{i + 1}"
        for dep in n.depends_on:
            if dep not in all_ids:
                raise ValueError(f"Node {nid}: depends_on '{dep}' not found in DAG")

    # Acyclicity check via DFS
    adj: dict[str, list[str]] = {nid: [] for nid in all_ids}
    for i, n in enumerate(nodes):
        nid = n.id or f"node-{i + 1}"
        for dep in n.depends_on:
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


def _agent_config_for_backend(backend: str, members: list[str]) -> str:
    if members:
        return members[0]
    mapping = {
        "hermes": "hermes:agent",
        "opencode": "opencode:backend-executor",
        "opencode_omo": "opencode:backend-omo",
        "claude_code": "claude-code:agent",
        "codex": "codex:agent",
        "aionui": "aionui:orchestrator",
    }
    return mapping.get(backend, "opencode:backend-executor")


def _persist_plan_clarification(
    pid: str,
    project_id: str,
    goal: str,
    user_intent: str,
    plan_status: str,
    clarify_context: str,
    clarify_rounds: int,
    partial_meta_goal: str,
) -> None:
    """Persist a plan in clarification state (File 06) before ratification."""
    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        return
    try:
        _ensure_project_in_db(db_url, project_id)  # FK: plans.project_id → projects
        with psycopg.connect(db_url) as c:
            with c.cursor() as cur:
                cur.execute(
                    """INSERT INTO plans
                       (plan_id, project_id, user_intent, goal, dag, plan_status,
                        clarify_context, clarify_rounds, partial_meta_goal, ratified, version)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (plan_id) DO UPDATE SET
                         plan_status = EXCLUDED.plan_status,
                         clarify_context = EXCLUDED.clarify_context,
                         clarify_rounds = EXCLUDED.clarify_rounds,
                         partial_meta_goal = EXCLUDED.partial_meta_goal
                    """,
                    (
                        pid, project_id, user_intent, goal,
                        json.dumps([]),    # empty dag
                        plan_status,
                        clarify_context,
                        clarify_rounds,
                        partial_meta_goal,
                        False,              # not ratified
                        1,
                    ),
                )
            c.commit()
    except Exception as exc:
        logger.warning("Failed to persist clarification state: %s", exc)


def _persist_plan_dag(plan_data: dict[str, Any]) -> None:
    """Persist plan nodes to DB (UPSERT). Handles both new plans and
    clarification-state plans that already exist with an empty DAG."""
    import os
    from backend.planning.schema import NodeMember, TaskSpec, NodeSuccess, SuccessCriterion
    from backend.evaluator.generate import generate_checks

    raw_nodes = plan_data.get("nodes", [])
    if not raw_nodes:
        logger.warning("No nodes to persist for plan %s", plan_data.get("plan_id"))
        return

    project_id = plan_data.get("project_id") or "default"
    quality_intent = plan_data.get("quality_intent")
    db_url = os.environ.get("DATABASE_URL", "")
    if db_url:
        _ensure_project_in_db(db_url, project_id)

    def _to_members(n: dict) -> list[NodeMember]:
        raw = n.get("members", [])
        if raw and isinstance(raw[0], dict):
            return [NodeMember(**m) if isinstance(m, dict) else NodeMember(agent_config=m, backend=n.get("backend", "opencode"), role=n.get("role", "executor")) for m in raw]
        backend = n.get("backend", "opencode")
        agent_cfg = n.get("agent_config_id") or "opencode:backend-executor"
        role = n.get("role", "executor")
        return [NodeMember(agent_config=agent_cfg, backend=backend, role=role)]

    def _task_from_node(n: dict, i: int) -> TaskSpec:
        task_raw = n.get("task")
        if isinstance(task_raw, dict):
            return TaskSpec(
                text=task_raw.get("text", n.get("description", n.get("title", ""))),
                inputs=task_raw.get("inputs", []),
                deliverables=task_raw.get("deliverables", []),
            )
        return TaskSpec(
            text=n.get("description") or n.get("title", f"node-{i}"),
            inputs=n.get("inputs", []),
            deliverables=n.get("deliverables", []),
        )

    def _success_from_node(n: dict) -> NodeSuccess:
        success_raw = n.get("success")
        if isinstance(success_raw, dict):
            return NodeSuccess(text=success_raw.get("text", ""))
        return NodeSuccess(text=n.get("success_criterion") or n.get("success", "Complete the task"))

    def _members_list(n: dict) -> list[str]:
        raw = n.get("members", [])
        agent_cfg = n.get("agent_config_id") or "opencode:backend-executor"
        if not raw:
            return [agent_cfg]
        if isinstance(raw[0], dict):
            return [m.get("agent_config", agent_cfg) for m in raw]
        return raw

    dag: list[PlanNode] = []
    total = len(raw_nodes)
    for i, n in enumerate(raw_nodes):
        nid = n.get("node_id") or n.get("id", f"node-{i}")
        task_text = _task_from_node(n, i).text
        success_text = _success_from_node(n).text
        members_flat = _members_list(n)

        # Use existing LLM-generated checks from in-memory nodes when available.
        # The LLM check-generator (check_generator.py) produces per-node checks
        # with full DAG context. Do NOT regenerate — the heuristic path cannot
        # match the LLM's per-node scoping.
        existing_raw = n.get("checks", [])
        if existing_raw:
            logger.info(
                "persist_plan_dag[%s/%s]: using %d existing LLM-generated checks (new flow)",
                nid, i, len(existing_raw),
            )
            from backend.evaluator.schema import Check as CheckSchema
            checks: list[CheckSchema] = []
            for c_raw in existing_raw:
                if isinstance(c_raw, CheckSchema):
                    checks.append(c_raw)
                elif isinstance(c_raw, dict):
                    checks.append(CheckSchema(**c_raw))
        else:
            logger.info(
                "persist_plan_dag[%s/%s]: no existing checks — fallback to heuristic gen (old flow)",
                nid, i,
            )
            # Fallback: no pre-existing checks (legacy path without meta-planner)
            generated = generate_checks(
                node_id=nid,
                task=task_text,
                success_criterion=success_text,
                node_index=i,
                total_nodes=total,
                members=members_flat,
                quality_intent=quality_intent,
            )
            checks = generated.checks

        pn = PlanNode(
            id=nid,
            members=_to_members(n),
            depends_on=n.get("depends_on", []),
            task=_task_from_node(n, i),
            success=_success_from_node(n),
            checks=checks,
            project_id=project_id,
        )
        dag.append(pn)

    plan_obj = Plan(
        plan_id=plan_data["plan_id"],
        project_id=project_id,
        user_intent=plan_data.get("description") or plan_data.get("title", ""),
        goal=plan_data.get("goal", ""),
        success=SuccessCriterion(text=plan_data.get("success", {}).get("text", "") if isinstance(plan_data.get("success"), dict) else ""),
        dag=dag,
        ratified=True,
    )
    persist_plan(plan_obj, ratified=True)


# ── Run endpoints ─────────────────────────────────────────────────

@router.post("/{plan_id}/runs")
async def create_run(plan_id: str, req: CreateRunRequest = CreateRunRequest()):
    """Create a new run from a ratified plan. Returns the run."""
    plan_data = _get_or_load_plan(plan_id)
    if not plan_data:
        raise HTTPException(404, "Plan not found")
    if not plan_data.get("ratified"):
        raise HTTPException(400, "Plan must be ratified before creating runs")

    run_id = f"run_{uuid.uuid4().hex[:8]}"
    run = {
        "id": run_id,
        "plan_id": plan_id,
        "state": "created",
        "note": req.note,
    }
    save_run(run)
    run_entry = get_run(run_id)
    return _ui_run_from_db(run_entry)


def _ui_run_from_db(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        "run_id": row["id"],
        "plan_id": row["plan_id"],
        "state": row["state"],
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        "approved_at": row["approved_at"].isoformat() if row.get("approved_at") else None,
        "finished_at": row["finished_at"].isoformat() if row.get("finished_at") else None,
        "worktree_root": row.get("worktree_root"),
        "note": row.get("note"),
    }


@router.get("/{plan_id}/runs")
async def list_plan_runs(plan_id: str):
    """List all runs for a plan."""
    runs = list_runs(plan_id)
    return [_ui_run_from_db(r) for r in runs]


@router.get("/runs/{run_id}")
async def get_run_endpoint(run_id: str):
    """Get a single run by ID."""
    row = get_run(run_id)
    if not row:
        raise HTTPException(404, "Run not found")
    return _ui_run_from_db(row)


@router.get("/runs/{run_id}/sessions")
async def get_run_sessions(run_id: str):
    """Get all node_sessions for a run."""
    row = get_run(run_id)
    if not row:
        raise HTTPException(404, "Run not found")
    return get_node_sessions(run_id)


@router.post("/runs/{run_id}/approve")
async def approve_run(run_id: str):
    """Approve a run — transitions state created -> approved.

    Does NOT spawn yet. Spawning happens via start_run.
    """
    row = get_run(run_id)
    if not row:
        raise HTTPException(404, "Run not found")
    if row["state"] != "created":
        raise HTTPException(400, f"Run is in state '{row['state']}', expected 'created'")
    update_run_state(run_id, "approved")
    return get_run(run_id)


@router.post("/runs/{run_id}/start")
async def start_run(run_id: str):
    """Start a run — spawns node sessions and transitions state -> running.

    Requires run state = 'approved'. Spawns the first ready node;
    the watcher owns subsequent advancement.
    """
    row = get_run(run_id)
    if not row:
        raise HTTPException(404, "Run not found")
    if row["state"] != "approved":
        raise HTTPException(400, f"Run must be 'approved' to start, got '{row['state']}'")

    update_run_state(run_id, "running")

    from backend.orchestration.runner import launch_run
    plan_data = _get_or_load_plan(row["plan_id"])
    if not plan_data:
        raise HTTPException(404, "Plan not found for run")

    try:
        import asyncio
        session_id = await asyncio.to_thread(launch_run, run_id, row, plan_data)
        return {"run_id": run_id, "state": "running", "session_id": session_id}
    except Exception as exc:
        update_run_state(run_id, "failed")
        # File 08: quarantine failed run
        try:
            from backend.worktree.lifecycle import finalize_failure
            finalize_failure(run_id, reason=str(exc))
        except Exception as lfe:
            logger.warning("finalize_failure for run %s: %s", run_id, lfe)
        import traceback
        traceback.print_exc()
        raise HTTPException(500, f"Failed to start run: {exc}")


# ── Legacy endpoints (node management) ────────────────────────────

@router.post("/{plan_id}/nodes")
async def append_node(plan_id: str, req: AppendNode):
    plan = _get_or_load_plan(plan_id)
    if not plan:
        raise HTTPException(404, "Plan not found")
    node = {
        "node_id": f"node-{len(plan['nodes']) + 1}",
        "title": req.title,
        "description": req.description,
        "depends_on": req.depends_on or [],
        "status": "pending",
    }
    if req.members:
        node["members"] = req.members
    elif req.agent_config_id:
        node["members"] = [req.agent_config_id]
        node["agent_config_id"] = req.agent_config_id
    if req.success_criterion:
        node["success_criterion"] = req.success_criterion
    plan["nodes"].append(node)
    return plan


@router.put("/{plan_id}/nodes/{node_id}")
async def update_plan_node(plan_id: str, node_id: str, req: AppendNode):
    plan = _get_or_load_plan(plan_id)
    if not plan:
        raise HTTPException(404, "Plan not found")
    for node in plan["nodes"]:
        if node["node_id"] == node_id:
            if req.members is not None:
                node["members"] = req.members
            if req.depends_on is not None:
                node["depends_on"] = req.depends_on
            if req.success_criterion is not None:
                node["success_criterion"] = req.success_criterion
            if req.title:
                node["title"] = req.title
            if req.description:
                node["description"] = req.description
            return plan
    raise HTTPException(404, "Node not found in plan")


@router.post("/{plan_id}/refine")
async def refine_plan(plan_id: str, req: RefineRequest):
    plan_data = _get_or_load_plan(plan_id)
    if not plan_data:
        raise HTTPException(404, "Plan not found")
    try:
        from backend.planning.brain import refine_plan as brain_refine
        updated_plan = brain_refine(
            plan_data=plan_data,
            instruction=req.instruction,
            image_data=req.image_data,
        )
        if updated_plan:
            _plans[plan_id] = updated_plan
    except Exception as exc:
        import traceback
        traceback.print_exc()
        pass
    return _plans[plan_id]


# ── Brain decomposition (used at propose time, not approve) ───────

_NODE_TITLE_TO_AGENT_CONFIG: list[tuple[str, str, tuple[str, str]]] = [
    ("planner", "planner", ("finance-planner", "planner")),
    ("executor", "executor", ("finance-fullstack-executor", "executor")),
    ("reviewer", "reviewer", ("finance-reviewer", "reviewer")),
    ("orchestrator", "orchestrator", ("orchestrator", "orchestrator")),
]


def _agent_config_for_node(node: dict[str, Any], db_url: str | None = None) -> tuple[str, str]:
    members = node.get("members", [])
    if members:
        first_member = members[0]
        if db_url:
            try:
                from backend.db.queries import get_agent_config
                cfg = get_agent_config(first_member)
                if cfg:
                    return first_member, cfg["role"]
            except Exception:
                pass
        return first_member, node.get("role", "executor")

    explicit = node.get("agent_config_id")
    if explicit:
        if db_url:
            try:
                from backend.db.queries import get_agent_config
                cfg = get_agent_config(explicit)
                if cfg:
                    return explicit, cfg["role"]
            except Exception:
                pass
        return explicit, node.get("role", "executor")

    title_lower = node.get("title", "").lower()
    for keyword, _, (ac, role) in _NODE_TITLE_TO_AGENT_CONFIG:
        if keyword in title_lower:
            return ac, role

    return "opencode:backend-executor", "executor"


def _extract_project(description: str) -> str | None:
    m = re.search(r"project\s+([a-z0-9][a-z0-9-]*)", description)
    return m.group(1) if m else None


def _ensure_project_in_db(db_url: str, project_id: str) -> None:
    with psycopg.connect(db_url) as c:
        with c.cursor() as cur:
            cur.execute(
                "INSERT INTO projects (project_id, name, repo_path) VALUES (%s, %s, %s) "
                "ON CONFLICT (project_id) DO NOTHING",
                (project_id, project_id, f"/opt/aipc/conductor/workspace/{project_id}"),
            )
        c.commit()


def _create_session_in_db(
    db_url: str, session_id: str, project_id: str, user_intent: str, node: PlanNode
) -> None:
    with psycopg.connect(db_url) as c:
        with c.cursor() as cur:
            cur.execute(
                "INSERT INTO sessions (session_id, project_id, user_intent, base_branch) "
                "VALUES (%s, %s, %s, %s)",
                (session_id, project_id, user_intent, "main"),
            )
        c.commit()


def _fetch_agent_configs(db_url: str) -> list[dict[str, str]]:
    try:
        with psycopg.connect(db_url) as c:
            with c.cursor() as cur:
                cur.execute(
                    "SELECT agent_config_id, role FROM agent_configs ORDER BY agent_config_id"
                )
                return [
                    {"agent_config_id": row[0], "role": row[1]}
                    for row in cur.fetchall()
                ]
    except Exception:
        return [
            {"agent_config_id": "opencode:backend-planner", "role": "planner"},
            {"agent_config_id": "opencode:backend-executor", "role": "executor"},
            {"agent_config_id": "opencode:backend-reviewer", "role": "reviewer"},
        ]
