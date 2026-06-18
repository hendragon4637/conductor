"""Mine recurring rubric-level failure patterns from evaluator scores.

Self-Harness discipline (File 04):
- Mine recurring failures (not one-offs).
- Cluster by rubric item so proposals target the dominant pattern.
- Reads ``goal_review`` scores from Langfuse (the evaluator's L2 score).
"""
from __future__ import annotations

import os
from collections import defaultdict
from typing import Any

from backend.ratchet.detect import _get_scores, _get_trace


def mine_failures(
    agent_config_id: str,
    min_count: int = 2,
    max_patterns: int = 5,
    score_threshold: float = 0.7,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Mine recurring rubric-level failure patterns from recent evaluator scores.

    Fetches recent ``goal_review`` scores from Langfuse for the given
    ``agent_config_id``, extracts rubric-level failures (items that scored
    ``FAIL`` in the L2 judge comment), clusters by rubric check id, and
    returns patterns that appear **more than once** (recurring, not one-offs).

    Args:
        agent_config_id: Filter traces by this agent config.
        min_count: Minimum occurrences to consider a pattern "recurring"
                   (default 2 — skip one-offs).
        max_patterns: Maximum number of failure patterns to return
                      (default 5).
        score_threshold: Only consider traces whose overall ``goal_review``
                         score is below this threshold (default 0.7).
        limit: Maximum number of ``goal_review`` score records to fetch
               from Langfuse (default 200).

    Returns:
        A list of dicts, each with keys:
        - ``pattern``: The rubric item that failed (str).
        - ``check_id``: The rubric check id (str).
        - ``count``: Number of occurrences (int).
        - ``sample_trace_ids``: List of trace ids where this pattern was
          observed (list[str]).
        - ``avg_score``: Average ``goal_review`` score for traces matching
          this pattern (float).
    """
    scores = _get_scores(name="goal_review", limit=limit)

    # Group by trace_id
    trace_scores: dict[str, list[dict]] = defaultdict(list)
    for s in scores:
        tid = s.get("traceId")
        if not tid:
            continue
        val = s.get("value")
        if val is None:
            continue
        trace_scores[tid].append({"value": float(val), "comment": s.get("comment", "")})

    # For each trace, resolve agent_config and extract rubric failures
    # rubric_failures[check_id] = {"count": int, "trace_ids": [...], "scores": [...]}
    rubric_failures: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"count": 0, "trace_ids": [], "scores": []}
    )
    seen_traces: set[str] = set()

    for tid, entries in trace_scores.items():
        if tid in seen_traces:
            continue
        seen_traces.add(tid)

        try:
            trace = _get_trace(tid)
        except Exception:
            continue

        meta = trace.get("metadata", {})
        cfg = meta.get("agent_config", "") or ""
        if cfg != agent_config_id:
            continue

        for entry in entries:
            score_val = entry["value"]
            comment = entry.get("comment", "")

            # Skip traces that pass overall
            if score_val >= score_threshold:
                continue

            # Parse rubric-level failures from the comment
            # Comment format (from run_l2): "check_id: FAIL (explanation) | ..."
            parts = comment.split(" | ")
            for part in parts:
                part = part.strip()
                if "FAIL" not in part:
                    continue
                # Extract check_id before the colon
                check_id = part.split(":")[0].strip()
                if not check_id:
                    continue
                record = rubric_failures[check_id]
                record["count"] += 1
                if tid not in record["trace_ids"]:
                    record["trace_ids"].append(tid)
                record["scores"].append(score_val)

    # Convert to sorted list, filter by min_count
    results: list[dict[str, Any]] = []
    for check_id, data in rubric_failures.items():
        if data["count"] < min_count:
            continue
        avg_s = sum(data["scores"]) / len(data["scores"]) if data["scores"] else 0.0
        results.append({
            "pattern": check_id,
            "check_id": check_id,
            "count": data["count"],
            "sample_trace_ids": data["trace_ids"][:5],
            "avg_score": round(avg_s, 4),
        })

    results.sort(key=lambda r: r["count"], reverse=True)
    return results[:max_patterns]
