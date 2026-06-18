"""Rubric loading and selection logic.

Loads preset rubric YAML files from ``rubrics/`` directory.
``select_rubric`` picks the best rubric for a node based on role + task text.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml


def _rubrics_dir() -> Path:
    return Path(os.path.dirname(__file__)) / "rubrics"


def load_all_rubrics() -> list[dict[str, Any]]:
    """Load all rubric YAML files from the rubrics directory."""
    rubrics_dir = _rubrics_dir()
    rubrics: list[dict[str, Any]] = []
    if not rubrics_dir.is_dir():
        return rubrics
    for fname in sorted(rubrics_dir.glob("*.yaml")):
        try:
            with open(fname) as f:
                data = yaml.safe_load(f)
            if data and isinstance(data, dict) and "items" in data:
                rubrics.append(data)
        except Exception:
            pass
    return rubrics


def load_rubric(name: str) -> dict[str, Any] | None:
    """Load a single rubric by name."""
    for r in load_all_rubrics():
        if r.get("name") == name:
            return r
    return None


def select_rubric(node_members: list[str | dict[str, Any]], task_text: str = "") -> dict[str, Any]:
    """Select the best rubric for a node based on its members' roles and task.

    Accepts both dicts (with a ``role`` key) and raw strings (agent IDs like
    ``"opencode:backend-executor"``, from which the segment after the colon or
    the whole string is used as the role).

    Priority:
    1. Exact role match (e.g. executor -> code_implementation).
    2. Keyword match in task text (e.g. "build the API" contains "api").
    3. Fallback to generic_quality.

    Args:
        node_members: List of member dicts or raw agent ID strings.
        task_text: The node's task description (lowered for matching).

    Returns:
        A rubric dict with ``name``, ``applies_to``, and ``items``.
    """
    task_lower = task_text.lower()
    roles: set[str] = set()
    for m in node_members:
        if isinstance(m, dict):
            raw = m.get("role", "")
        else:
            # "opencode:backend-executor" -> "backend-executor"
            raw = m.split(":", 1)[1].lower() if ":" in m else m.lower()
        if raw:
            roles.add(raw)
            # Compound role segments: "backend-executor" -> "backend", "executor"
            roles.update(raw.split("-"))
    all_rubrics = load_all_rubrics()

    # Priority 1: match by role — pick rubric with most matching role tokens
    best_match: dict[str, Any] | None = None
    best_score = 0
    for r in all_rubrics:
        applies_to = {a.lower() for a in r.get("applies_to", [])}
        if "default" in applies_to:
            continue
        matched = len(roles & applies_to)
        if matched > best_score:
            best_score = matched
            best_match = r
    if best_match:
        return best_match

    # Priority 2: match by keyword in task text
    for r in all_rubrics:
        applies_to = {a.lower() for a in r.get("applies_to", [])}
        if "default" in applies_to:
            continue
        if any(kw in task_lower for kw in applies_to):
            return r

    # Priority 3: fallback
    fallback = load_rubric("generic_quality")
    if fallback:
        return fallback
    return {"name": "generic_quality", "applies_to": ["default"], "items": []}
