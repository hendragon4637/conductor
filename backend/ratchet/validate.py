"""Validation — held-out comparison for ratchet mutations.

Three stages (Self-Harness):
  1. **baseline_score** — mean ``goal_review`` over recent held-out runs
     for this agent_config, read from Langfuse.
  2. **candidate_score** — apply mutation in an experiment worktree, run the
     golden/validation set, collect scores via the evaluator L2 judge.
  3. **validate** — full loop: baseline → apply → candidate → compare.
     A candidate is kept only if it beats the baseline on a HELD-OUT set
     (not just the mining set).

File 04: Ratchet consumes the evaluator's ``goal_review`` Langfuse score,
not the watcher verdict.
"""
from __future__ import annotations

import os
from typing import Any

from backend.ratchet.detect import _get_scores, _get_trace


def baseline_score(
    agent_config_id: str,
    dataset: str | None = None,
    window: int = 50,
) -> float:
    """Mean ``goal_review`` over recent runs for this agent config.

    Args:
        agent_config_id: The agent config identifier.
        dataset: Optional dataset name filter (experiments store ``dataset``).
        window: Number of recent score records to consider (default 50).

    Returns:
        Mean ``goal_review`` score (0.0–1.0). Returns 0.0 if no scores found.
    """
    scores = _get_scores(name="goal_review", limit=window)

    vals: list[float] = []
    seen: set[str] = set()

    for s in scores:
        tid = s.get("traceId")
        if not tid or tid in seen:
            continue
        seen.add(tid)

        val = s.get("value")
        if val is None:
            continue

        # Resolve agent_config from trace metadata
        try:
            trace = _get_trace(tid)
        except Exception:
            continue
        meta = trace.get("metadata", {})
        cfg = meta.get("agent_config", "") or ""
        if cfg != agent_config_id:
            continue
        if dataset:
            trace_dataset = meta.get("dataset", "") or ""
            if trace_dataset != dataset:
                continue

        vals.append(float(val))

        if len(vals) >= window:
            break

    if not vals:
        return 0.0
    return round(sum(vals) / len(vals), 4)


def candidate_score(
    agent_config_id: str,
    mutation: dict[str, Any],
    dataset: str | None = None,
    max_tasks: int = 0,
) -> dict[str, Any]:
    """Run a candidate experiment and return the mean ``goal_review`` score.

    Applies the mutation in an experiment worktree, runs the golden/validation
    set, and collects scores via the evaluator L2 judge (which writes
    ``goal_review`` to Langfuse).

    Args:
        agent_config_id: The agent config to test.
        mutation: Dict with keys ``target`` and ``candidate``.
        dataset: Optional dataset name; defaults to agent_config_id.
        max_tasks: Max golden tasks to run (0 = all).

    Returns:
        Dict with ``experiment_id``, ``baseline_score``, ``candidate_score``,
        ``delta``, and ``task_results``.
    """
    from backend.ratchet.experiment import run_experiment

    return run_experiment(
        agent_config=agent_config_id,
        mutation=mutation,
        dataset=dataset,
        max_tasks=max_tasks,
    )


def validate(
    agent_config_id: str,
    mutation: dict[str, Any],
    held_out: list[str] | None = None,
    delta_threshold: float = 0.03,
) -> dict[str, Any]:
    """Full validation loop: baseline → apply → candidate → compare.

    Runs an experiment (baseline + candidate on the golden set), then
    additionally checks that no regression occurs on the **held-out** tasks
    (those NOT in the mining set).

    Args:
        agent_config_id: The agent config to validate.
        mutation: Dict with ``target`` and ``candidate``.
        held_out: Optional list of task filenames to treat as held-out.
                  If provided, ensures no regression on these tasks.
        delta_threshold: Minimum score improvement to keep (default 0.03).

    Returns:
        Dict with keys:
        - ``decision``: ``"keep"`` or ``"revert"``
        - ``experiment_id``: str
        - ``baseline_score``: float
        - ``candidate_score``: float
        - ``delta``: float
        - ``held_out_regressed``: bool (only if ``held_out`` is provided)
        - ``task_results``: list of per-task results
    """
    result = candidate_score(agent_config_id, mutation)

    baseline = result.get("baseline_score", 0.0)
    candidate = result.get("candidate_score", 0.0)
    delta = candidate - baseline

    # Held-out regression check
    held_out_regressed = False
    if held_out and result.get("task_results"):
        for tr in result["task_results"]:
            task_file = tr.get("task", "")
            if task_file in held_out:
                b = tr.get("baseline_score", 0.0) or 0.0
                c = tr.get("candidate_score", 0.0) or 0.0
                if c < b - 0.01:  # tolerance for floating point
                    held_out_regressed = True
                    break

    # Decision: must beat threshold AND not regress on held-out
    if delta >= delta_threshold and not held_out_regressed:
        decision = "keep"
    else:
        decision = "revert"

    return {
        "decision": decision,
        "experiment_id": result.get("experiment_id", ""),
        "baseline_score": baseline,
        "candidate_score": candidate,
        "delta": round(delta, 4),
        "held_out_regressed": held_out_regressed,
        "task_results": result.get("task_results", []),
    }
