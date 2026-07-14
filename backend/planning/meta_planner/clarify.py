from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any

import psycopg
from psycopg.rows import dict_row

from backend.planning.meta_planner.goal_formulator import MetaGoal, formulate

logger = logging.getLogger(__name__)

MAX_CLARIFY_ROUNDS = 3


@dataclass
class ClarifyPending:
    """Returned when a plan cannot proceed without human answers."""
    questions: list[str] = field(default_factory=list)
    reason: str = ""


def condense(clarify_context: list[dict]) -> str:
    """Condense multi-turn Q&A history into a compact prompt string."""
    parts = []
    for round_ctx in clarify_context:
        r = round_ctx.get("round", 0)
        qs = "; ".join(round_ctx.get("questions", []))
        ans = round_ctx.get("answers")
        if ans:
            parts.append(f"Round {r} — You asked: {qs}. They answered: {ans}.")
        else:
            parts.append(f"Round {r} — You asked: {qs}. (awaiting answer)")
    return "\n".join(parts)


def _get_db() -> str:
    return os.environ["DATABASE_URL"]


def _load_plan(plan_id: str) -> dict[str, Any] | None:
    db_url = _get_db()
    with psycopg.connect(db_url, row_factory=dict_row) as c:
        with c.cursor() as cur:
            cur.execute("SELECT * FROM plans WHERE plan_id = %s", (plan_id,))
            row = cur.fetchone()
    if row and isinstance(row.get("clarify_context"), str):
        row["clarify_context"] = json.loads(row["clarify_context"])
    if row and isinstance(row.get("partial_meta_goal"), str):
        row["partial_meta_goal"] = json.loads(row["partial_meta_goal"])
    return row


def _update_plan(plan_id: str, **kwargs) -> None:
    if not kwargs:
        return
    db_url = _get_db()
    sets = ", ".join(f"{k} = %s" for k in kwargs)
    vals = list(kwargs.values())
    with psycopg.connect(db_url) as c:
        with c.cursor() as cur:
            cur.execute(
                f"UPDATE plans SET {sets} WHERE plan_id = %s",
                (*vals, plan_id),
            )
        c.commit()


def _defer_goal(plan_id: str, reason: str) -> None:
    _update_plan(plan_id, plan_status="draft", partial_meta_goal=None)
    logger.warning("Plan %s deferred: %s", plan_id, reason)


def formulate_or_clarify(
    plan_id: str,
    new_answer: str | None = None,
) -> MetaGoal | ClarifyPending:
    """Multi-turn clarification state machine (File 06).

    Loads a plan, optionally folds in a human answer, re-formulates
    with full prior Q&A context. Returns ``MetaGoal`` when resolved
    (plan_status → formulated) or ``ClarifyPending`` when more input
    is needed (plan_status → awaiting_clarification).

    Args:
        plan_id: The plan to formulate/clarify.
        new_answer: If provided, the human's answer to the current
            open questions (attached to the last round).

    Returns:
        ``MetaGoal`` when formulation succeeds.
        ``ClarifyPending`` when human input is still needed.
    """
    plan = _load_plan(plan_id)
    if not plan:
        raise ValueError(f"Plan {plan_id} not found")

    user_intent = plan.get("user_intent") or plan.get("goal") or ""
    origin = "human"

    # fold a new human answer into the stored context
    if new_answer is not None:
        ctx = list(plan.get("clarify_context") or [])
        if ctx and ctx[-1].get("answers") is None:
            ctx[-1]["answers"] = new_answer
        _update_plan(
            plan_id,
            clarify_context=json.dumps(ctx),
            clarify_rounds=(plan.get("clarify_rounds") or 0) + 1,
        )
        plan = _load_plan(plan_id)

    # re-formulate with FULL prior context
    prior = condense(plan.get("clarify_context") or [])
    mg = formulate(raw_input=user_intent, origin=origin, prior=prior)

    if mg.needs_clarification:
        rounds = plan.get("clarify_rounds") or 0
        if rounds >= MAX_CLARIFY_ROUNDS:
            if origin == "human":
                _update_plan(
                    plan_id,
                    plan_status="awaiting_clarification",
                    partial_meta_goal=mg.model_dump_json(),
                )
            else:
                _defer_goal(plan_id, "clarify cap reached")
            return ClarifyPending(questions=mg.questions, reason="clarify cap reached")

        # open a new round, persist questions, PAUSE
        ctx = list(plan.get("clarify_context") or [])
        ctx.append({
            "round": rounds + 1,
            "questions": mg.questions,
            "answers": None,
        })
        _update_plan(
            plan_id,
            plan_status="awaiting_clarification",
            clarify_context=json.dumps(ctx),
            partial_meta_goal=mg.model_dump_json(),
        )
        logger.info(
            "Plan %s now awaiting_clarification (round %d): %s",
            plan_id, rounds + 1, mg.questions,
        )
        return ClarifyPending(questions=mg.questions)

    # resolved → proceed
    # NOTE: keep partial_meta_goal — the caller invokes the LangGraph which
    # overwrites it via _n_generate_plan.  Clearing it here creates a window
    # where _get_meta_goal() returns empty spec/quality_intent if the graph
    # invocation fails before _n_generate_plan writes it back.
    _update_plan(plan_id, plan_status="formulated")
    return mg
