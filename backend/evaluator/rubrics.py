"""Rubric loading and selection logic.

Loads preset rubric YAML files from ``rubrics/`` directory or from the
``rubrics`` table in PostgreSQL (DB preferred, YAML as fallback).
``select_rubric`` picks the best rubric for a node based on role + task text.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import yaml


def _rubrics_dir() -> Path:
    return Path(os.path.dirname(__file__)) / "rubrics"


def _row_to_rubric(row: dict[str, Any]) -> dict[str, Any]:
    rubric = {"name": row["name"], "applies_to": row["applies_to"], "items": row["items"]}
    if "tier" in row:
        rubric["tier"] = row["tier"]
    if not isinstance(rubric["applies_to"], (list, tuple)):
        rubric["applies_to"] = json.loads(rubric["applies_to"]) if isinstance(rubric["applies_to"], str) else []
    if not isinstance(rubric["items"], (list, tuple)):
        rubric["items"] = json.loads(rubric["items"]) if isinstance(rubric["items"], str) else []
    return rubric


def _load_all_from_db() -> list[dict[str, Any]]:
    try:
        from backend.db import get_connection
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                _ = cur.execute("SELECT name, applies_to, tier, items FROM rubrics ORDER BY name")
                return [_row_to_rubric(dict(r)) for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception:
        return []


def load_all_rubrics() -> list[dict[str, Any]]:
    """Load all rubrics — from DB registry first, falling back to YAML files."""
    db_rubrics = _load_all_from_db()
    if db_rubrics:
        return db_rubrics
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


def _select_best_rubric(
    rubrics: list[dict[str, Any]],
    node_members: list[str | dict[str, Any]],
    task_text: str = "",
) -> dict[str, Any] | None:
    task_lower = task_text.lower()
    roles: set[str] = set()
    for m in node_members:
        if isinstance(m, dict):
            raw = m.get("role", "")
        else:
            raw = m.split(":", 1)[1].lower() if ":" in m else m.lower()
        if raw:
            roles.add(raw)
            roles.update(raw.split("-"))

    best_match: dict[str, Any] | None = None
    best_score = 0
    for r in rubrics:
        applies_to = {a.lower() for a in r.get("applies_to", [])}
        if "default" in applies_to:
            continue
        matched = len(roles & applies_to)
        if matched > best_score:
            best_score = matched
            best_match = r
    if best_match:
        return best_match

    for r in rubrics:
        applies_to = {a.lower() for a in r.get("applies_to", [])}
        if "default" in applies_to:
            continue
        if any(kw in task_lower for kw in applies_to):
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
    all_rubrics = load_all_rubrics()
    result = _select_best_rubric(all_rubrics, node_members, task_text)
    if result:
        return result
    fallback = load_rubric("generic_quality")
    if fallback:
        return fallback
    return {"name": "generic_quality", "applies_to": ["default"], "items": []}


def retrieve_rubric_by_name(name: str) -> dict[str, Any] | None:
    try:
        from backend.db import get_connection
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                _ = cur.execute(
                    "SELECT name, applies_to, tier, items FROM rubrics WHERE name = %s",
                    (name,),
                )
                row = cur.fetchone()
                if row:
                    return _row_to_rubric(dict(row))
        finally:
            conn.close()
    except Exception:
        pass
    return None


def retrieve_rubric(node_members: list[str | dict[str, Any]], task_text: str = "") -> dict[str, Any]:
    try:
        from backend.db import get_connection
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                _ = cur.execute("SELECT name, applies_to, tier, items FROM rubrics ORDER BY name")
                rows = cur.fetchall()
                if rows:
                    db_rubrics = [_row_to_rubric(dict(r)) for r in rows]
                    result = _select_best_rubric(db_rubrics, node_members, task_text)
                    if result:
                        return result
        finally:
            conn.close()
    except Exception:
        pass
    return select_rubric(node_members, task_text)
