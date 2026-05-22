"""LangGraph nodes for the trace lifecycle."""
from __future__ import annotations
from typing import Any
from uuid import UUID

from backend.db import queries
from backend.graph.state import ConductorState
from backend.services.schema_validator import validate


# ───────────────────────── prepare_trace ─────────────────────────
def prepare_trace(state: ConductorState) -> ConductorState:
    """
    Pre-spawn node: validate, hash skill, insert trace row.
    Sets state.trace_id, state.skill_snapshot_hash.
    """
    cfg = queries.get_agent_config(state.agent_config_id)
    if cfg is None:
        state.errors.append(f"agent_config not found or inactive: {state.agent_config_id}")
        state.status = "failed"
        return state

    # Validate input_spec against expected schema
    schema_name = cfg.get("input_spec_schema")
    if schema_name and state.input_spec:
        errs = validate(schema_name, state.input_spec)
        if errs:
            state.errors.extend(f"input_spec invalid ({schema_name}): {e}" for e in errs)
            state.status = "failed"
            return state

    skill_path = cfg.get("skill_path")
    skill_hash = queries.hash_file(skill_path) if skill_path else None

    harness = cfg.get("harness") or cfg["cli"]
    trace_id = queries.insert_trace(
        task_id=state.task_id,
        agent_config_id=state.agent_config_id,
        role=cfg["role"],
        cli=harness,
        harness=harness,
        input_spec=state.input_spec,
        skill_path=skill_path,
        skill_snapshot_hash=skill_hash,
        skill_id=state.skill_id,
        skill_version=state.skill_version,
        preceding_trace_id=state.preceding_trace_id,
    )

    state.trace_id = trace_id
    state.skill_snapshot_hash = skill_hash
    state.skill_path = skill_path
    state.status = "spawned"  # actual subprocess spawn happens in file 08
    return state


# ───────────────────────── record_completion ─────────────────────────
def record_completion(state: ConductorState) -> ConductorState:
    """
    Post-CLI node: invoked once the adapter (file 07) detects completion.
    Validates output_spec, updates trace row.
    """
    if state.trace_id is None:
        state.errors.append("record_completion called without trace_id")
        return state

    cfg = queries.get_agent_config(state.agent_config_id)
    if cfg is None:
        state.errors.append(f"agent_config gone: {state.agent_config_id}")
        return state

    # If we got an output_spec, validate it
    if state.output_spec is not None:
        schema_name = cfg.get("output_spec_schema")
        if schema_name:
            errs = validate(schema_name, state.output_spec)
            if errs:
                state.errors.extend(f"output_spec invalid: {e}" for e in errs)
                state.status = "failed"
                state.ended_reason = "spec_invalid"

    final_status = state.status if state.status in ("failed",) else "complete"
    queries.update_trace_status(
        state.trace_id,
        status=final_status,
        output_spec=state.output_spec,
        ended_reason=state.ended_reason or ("completed" if final_status == "complete" else "spec_invalid"),
    )
    state.status = final_status
    return state


# ───────────────────────── route_next ─────────────────────────
def route_next(state: ConductorState) -> ConductorState:
    """
    Read agent_config.routing_rules. Decide next agent_config_id or termination.
    In week 1: always terminates (single-config, standalone pattern).
    """
    cfg = queries.get_agent_config(state.agent_config_id)
    if cfg is None:
        state.next_agent_config_id = None
        state.terminates_task = True
        return state

    rules = cfg.get("routing_rules") or {}
    bucket = "on_success" if state.status == "complete" else "on_failure"
    candidates = rules.get(bucket, []) or []

    chosen = None
    for rule in candidates:
        cond = (rule.get("condition") or "").strip()
        if not cond:
            chosen = rule
            break
        # Week 1: we don't implement condition evaluation yet.
        # Standalone config has empty conditions, so the first rule always matches.
        # Week 4+: implement a safe expression evaluator here.

    if chosen is None:
        # No rule matched
        state.next_agent_config_id = None
        state.terminates_task = bool(rules.get("terminates_task_if_none", False))
    else:
        state.next_agent_config_id = chosen.get("next_config")
        state.terminates_task = bool(chosen.get("terminates_task", False))

    # Persist the decision on the trace
    if state.trace_id:
        queries.update_trace_status(
            state.trace_id,
            terminates_task=state.terminates_task,
        )

    return state


# ───────────────────────── finalize_task ─────────────────────────
def finalize_task(state: ConductorState) -> ConductorState:
    """
    If terminates_task, mark the parent task as done/failed.
    """
    if not state.terminates_task:
        return state

    task = queries.get_task(state.task_id)
    if task is None:
        return state

    if state.status == "complete":
        queries.mark_task_status(state.task_id, "done", completion_signal="manual_done")
    else:
        queries.mark_task_status(state.task_id, "blocked", completion_signal=None)

    return state
