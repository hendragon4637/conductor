#!/usr/bin/env python3
"""Seed the rubric registry from YAML files + built-in plan_structure.

Reads all YAML rubric files from backend/evaluator/rubrics/ and inserts or
upserts them into the `rubrics` and `check_templates` tables.

Usage:
    python scripts/seed_rubrics.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import yaml

# Ensure project root is on sys.path
_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root))

from backend.db import get_connection

RUBRICS_DIR = _project_root / "backend" / "evaluator" / "rubrics"


def load_rubric_yamls() -> list[dict]:
    rubrics: list[dict] = []
    if not RUBRICS_DIR.is_dir():
        print(f"[seed] rubrics dir not found: {RUBRICS_DIR}")
        return rubrics
    for fpath in sorted(RUBRICS_DIR.glob("*.yaml")):
        try:
            with open(fpath) as f:
                data = yaml.safe_load(f)
            if data and isinstance(data, dict) and "items" in data:
                rubrics.append(data)
                print(f"[seed] loaded rubric: {data.get('name', '?')} from {fpath.name}")
        except Exception as exc:
            print(f"[seed] skip {fpath.name}: {exc}")
    return rubrics


def seed_rubrics(conn) -> None:
    rubrics = load_rubric_yamls()
    if not rubrics:
        print("[seed] no rubrics to seed — inserting built-in plan_structure")
        rubrics.append({
            "name": "plan_structure",
            "applies_to": ["planner", "plan"],
            "tier": "L2",
            "items": [
                {"id": "covers_goal", "rubric_item": "Do the nodes together fully cover the plan goal (nothing missing)?", "weight": 2.0},
                {"id": "right_sized", "rubric_item": "Is each node a bounded, single-responsibility unit (not too big/small)?", "weight": 1.5},
                {"id": "deps_correct", "rubric_item": "Are dependencies correct and minimal (no missing or spurious edges)?", "weight": 1.5},
                {"id": "measurable", "rubric_item": "Does each node have a measurable success criterion?", "weight": 1.0},
            ],
        })

    cur = conn.cursor()
    for r in rubrics:
        name = r.get("name", "unnamed")
        applies_to = r.get("applies_to", ["default"])
        tier = r.get("tier", "L2")
        items = r.get("items", [])
        cur.execute(
            """
            INSERT INTO rubrics (name, applies_to, tier, items, version, updated_at)
            VALUES (%s, %s::jsonb, %s, %s::jsonb, 1, now())
            ON CONFLICT (name) DO UPDATE SET
                applies_to = EXCLUDED.applies_to,
                tier = EXCLUDED.tier,
                items = EXCLUDED.items,
                version = rubrics.version + 1,
                updated_at = now()
            """,
            (name, _json(applies_to), tier, _json(items)),
        )
        print(f"[seed] rubric '{name}' upserted ({len(items)} items, tier={tier})")

    conn.commit()
    cur.close()


def seed_check_templates(conn) -> None:
    templates = [
        {
            "name": "tests_pass",
            "tier": "L1",
            "kind": "shell",
            "template": {"cmd": "cd {worktree} && python -m pytest {test_path} -x --tb=short -q 2>&1 | tail -20", "expect": "exit 0"},
        },
        {
            "name": "file_present",
            "tier": "L1",
            "kind": "file_exists",
            "template": {"expect": "file exists", "path": "{worktree}/{file_path}"},
        },
        {
            "name": "py_compile",
            "tier": "L1",
            "kind": "shell",
            "template": {"cmd": "python -m py_compile {worktree}/{file_path}", "expect": "exit 0"},
        },
    ]
    cur = conn.cursor()
    for t in templates:
        cur.execute(
            """
            INSERT INTO check_templates (name, tier, kind, template)
            VALUES (%s, %s, %s, %s::jsonb)
            ON CONFLICT (name) DO UPDATE SET
                tier = EXCLUDED.tier,
                kind = EXCLUDED.kind,
                template = EXCLUDED.template
            """,
            (t["name"], t["tier"], t["kind"], _json(t["template"])),
        )
        print(f"[seed] check_template '{t['name']}' upserted (kind={t['kind']})")
    conn.commit()
    cur.close()


def _json(val):
    """Serialize to JSON string for psycopg2."""
    import json
    return json.dumps(val)


def main():
    conn = get_connection()
    try:
        seed_rubrics(conn)
        seed_check_templates(conn)
        print("[seed] rubric registry seeded successfully")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
