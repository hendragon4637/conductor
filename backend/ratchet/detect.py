from __future__ import annotations

import base64
import json
import os
import urllib.request
from collections import defaultdict
from typing import Any


def _lazy_auth() -> str:
    """Return a Basic auth header sourced from env vars on every call.

    This is lazy so that callers (e.g. pytest) can load ``.env`` before
    the first call without worrying about import-time evaluation.
    """
    pk = os.environ.get("LANGFUSE_PUBLIC_KEY", "pk-lf-local")
    sk = os.environ.get("LANGFUSE_SECRET_KEY", "sk-lf-local")
    return base64.b64encode(f"{pk}:{sk}".encode()).decode()


def _lf_host() -> str:
    return os.environ.get("LANGFUSE_HOST", "http://127.0.0.1:3001")


def _get_json(url: str) -> dict[str, Any]:
    req = urllib.request.Request(
        url, headers={"Authorization": f"Basic {_lazy_auth()}"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _get_trace(trace_id: str) -> dict[str, Any]:
    return _get_json(f"{_lf_host()}/api/public/traces/{trace_id}")


def _get_scores(name: str = "goal_review", limit: int = 500) -> list[dict]:
    """Fetch scores from Langfuse public API."""
    page, scores = 1, []
    while True:
        data = _get_json(
            f"{_lf_host()}/api/public/scores"
            f"?name={name}&limit=100&page={page}"
        )
        scores.extend(data.get("data", []))
        if page >= data.get("meta", {}).get("totalPages", 1):
            break
        page += 1
        if len(scores) >= limit:
            break
    return scores[:limit]


def weak_configs(threshold: float = 0.7, min_runs: int = 5) -> list[str]:
    scores = _get_scores(name="goal_review")

    # Group by trace_id -> agent_config
    trace_scores: dict[str, list[float]] = defaultdict(list)
    seen_traces: set[str] = set()

    for s in scores:
        tid = s.get("traceId")
        if not tid:
            continue
        val = s.get("value")
        if val is None:
            continue
        trace_scores[tid].append(float(val))

    # Get trace metadata for agent_config
    config_scores: dict[str, list[float]] = defaultdict(list)

    for tid, vals in trace_scores.items():
        if tid in seen_traces:
            # Already have the config for this trace — skip duplicate
            continue
        seen_traces.add(tid)
        try:
            trace = _get_trace(tid)
        except Exception:
            continue
        meta = trace.get("metadata", {})
        agent_cfg = meta.get("agent_config") or "unknown"
        config_scores[agent_cfg].extend(vals)

    # Filter by threshold
    weak: list[str] = []
    for cfg, vals in config_scores.items():
        if len(vals) < min_runs:
            continue
        mean = sum(vals) / len(vals)
        if mean < threshold:
            weak.append(cfg)

    return weak


def failing_traces(
    agent_config: str | None = None, limit: int = 20
) -> list[dict]:
    """Return recent failing traces (goal_review score < 0.5)."""  # noqa: D205
    scores = _get_scores(name="goal_review", limit=200)

    failing: list[dict] = []
    for s in scores:
        val = s.get("value")
        if val is None or float(val) >= 0.5:
            continue
        if len(failing) >= limit:
            break
        tid = s.get("traceId")
        if not tid:
            continue
        try:
            trace = _get_trace(tid)
        except Exception:
            continue
        meta = trace.get("metadata", {})
        if agent_config and meta.get("agent_config") != agent_config:
            continue
        failing.append({
            "trace_id": tid,
            "score": float(val),
            "comment": s.get("comment", ""),
            "input": trace.get("input", {}),
            "output": trace.get("output", {}),
            "metadata": meta,
        })

    return failing
