"""MCP tool handlers — each maps to a Conductor backend operation.

These are the actual implementations behind the MCP tool surface.
All plan mutations produce PENDING plans — approval stays in the Conductor UI.
"""

from __future__ import annotations

import logging
from typing import Any

from backend.planning.store import get_plan as load_plan, save_plan
from backend.planning.decompose import decompose_or_update
from backend.planning.decomposed_spec import DecomposedPlan

logger = logging.getLogger(__name__)


async def handle_create_plan(
    intent: str,
    project: str,
    spec: str | None = None,
    quality_intent: str | None = None,
    nodes: str | None = None,
) -> dict[str, Any]:
    import json as _json
    import uuid
    plan_id = f"mcp-plan-{uuid.uuid4().hex[:12]}"

    if nodes is not None:
        # BYO-DAG path: parse JSON string, skip brain
        nodes_raw = _json.loads(nodes)
        if not isinstance(nodes_raw, list):
            return {"error": "nodes must be a JSON array of node objects"}
        dplan = decompose_or_update(
            plan_id=plan_id,
            source="byo_dag",
            payload={
                "nodes": nodes_raw,
                "quality_intent": quality_intent,
                "project_id": project,
            },
        )
    else:
        payload: dict[str, Any] = {
            "intent": intent,
            "project_id": project,
        }
        if spec is not None:
            payload["spec"] = spec
        if quality_intent is not None:
            payload["quality_intent"] = quality_intent

        dplan = decompose_or_update(
            plan_id=plan_id,
            source="new_plan",
            payload=payload,
        )

    save_plan(dplan.model_dump() if hasattr(dplan, "model_dump") else dplan)

    route = "BYO-DAG" if nodes is not None else "intent"
    msg = (
        f"Plan {plan_id} created via {route} with {len(dplan.chunks)} node(s) "
        f"and per-node checks."
    )
    if quality_intent:
        msg += f" Quality intent provided — human-intent checks are tagged with provenance='human_intent'."

    return {
        "plan_id": plan_id,
        "status": "pending",
        "checks_ratified": False,
        "chunks": len(dplan.chunks),
        "message": msg + " Ratify in the Conductor UI before execution.",
    }


async def handle_refine_plan(
    plan_id: str,
    instruction: str,
    quality_intent: str | None = None,
) -> dict[str, Any]:
    existing = load_plan(plan_id)
    if not existing:
        return {"error": f"Plan {plan_id} not found"}

    payload: dict[str, Any] = {
        "instruction": instruction,
        "intent": existing.get("user_intent", ""),
        "project_id": existing.get("project_id", "default"),
    }
    if quality_intent is not None:
        payload["quality_intent"] = quality_intent
    elif existing.get("quality_intent"):
        # Preserve quality_intent from the original plan if not overridden
        payload["quality_intent"] = existing["quality_intent"]

    dplan = decompose_or_update(
        plan_id=plan_id,
        source="refine",
        payload=payload,
    )

    save_plan(dplan.model_dump() if hasattr(dplan, "model_dump") else dplan)

    msg = f"Plan {plan_id} refined."
    if quality_intent:
        msg += f" Quality intent updated — human-intent checks are tagged with provenance='human_intent'."

    return {
        "plan_id": plan_id,
        "status": "pending",
        "checks_ratified": False,
        "chunks": len(dplan.chunks),
        "message": msg + " Re-ratification required.",
    }


async def handle_get_plan(plan_id: str) -> dict[str, Any]:
    plan = load_plan(plan_id)
    if not plan:
        return {"error": f"Plan {plan_id} not found"}
    return dict(plan)


async def handle_list_sessions() -> list[dict[str, Any]]:
    try:
        from backend.db import queries as db_q
        with db_q.conn() as c, c.cursor() as cur:
            cur.execute(
                "SELECT session_id, project_id, user_intent, status, created_at "
                "FROM sessions ORDER BY created_at DESC LIMIT 50"
            )
            rows = cur.fetchall()
            return [dict(row) for row in rows]
    except Exception as exc:
        logger.debug("list_sessions failed: %s", exc)
        return {"error": "Could not list sessions", "detail": str(exc)}


async def handle_search_memory(query: str, scope: str = "product") -> dict[str, Any]:
    try:
        if scope == "meta":
            from backend.evaluator.memory_integration import ground_meta_evaluation
            violations = ground_meta_evaluation(query)
            return {
                "scope": "meta",
                "results": violations,
            }
        else:
            from backend.evaluator.memory_integration import ground_checks_with_memory
            checks = ground_checks_with_memory(task=query)
            return {
                "scope": "product",
                "results": [chk.criterion for chk in checks],
            }
    except Exception as exc:
        logger.debug("search_memory failed: %s", exc)
        return {"scope": scope, "error": str(exc), "results": []}
