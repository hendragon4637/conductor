"""Formulator metrics — the cycle-1 ruler.

TARGET is node_accuracy; the other two are guards. Standards comparison is
exact set equality on BASE names: version suffixes (``-v2``), ``@subdir``
and ``[variant]`` are stripped on BOTH sides, so ``design-layout`` equals
``design-layout-v2`` (settled rule — a version bump must not be a false miss).
"""
from __future__ import annotations

import re
from statistics import mean
from typing import Any

TARGET = "node_accuracy"
GUARDS = ["standard_accuracy", "clarify_accuracy"]


def base(item: str) -> str:
    """'design-layout-v2@design[technical-dense]' -> 'design-layout'."""
    s = item.split("@")[0].strip()
    s = re.sub(r"-v\d+$", "", s)
    return s


def grade_row(expected: dict[str, Any], actual: dict[str, Any]) -> dict[str, Any]:
    exp_std = sorted({base(s) for s in expected.get("standards", [])})
    act_std = sorted({base(s) for s in actual.get("standards", [])})
    return {
        "standard": exp_std == act_std,
        "node": expected["nodes_min"] <= actual["estimated_nodes"] <= expected["nodes_max"],
        "clarify": expected["clarify"] == actual["clarify"],
    }


def aggregate(hits: list[dict[str, Any]]) -> dict[str, float]:
    return {f"{k}_accuracy": mean(h[k] for h in hits) for k in ("standard", "node", "clarify")}
