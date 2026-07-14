#!/usr/bin/env python3
"""Seed check_templates with promptfoo assertion taxonomy.

Inserts 9 L1 presets (contains, not-contains, regex, is-json, json-schema,
equals, starts-with, exec, file-exists) as artifact_text kind, tagged with
provenance=promptfoo-taxonomy in the template JSONB.

Usage:
    uv run python scripts/seed_check_templates_promptfoo.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root))

# Load env before importing backend modules
from dotenv import load_dotenv

load_dotenv(Path(_project_root) / ".env")

from backend.db import get_connection as get_conn

PROMPTFOO_PRESETS: list[dict] = [
    {
        "name": "contains",
        "tier": "L1",
        "kind": "artifact_text",
        "template": {
            "expect": "artifact contains substring",
            "provenance": "promptfoo-taxonomy",
        },
    },
    {
        "name": "not-contains",
        "tier": "L1",
        "kind": "artifact_text",
        "template": {
            "expect": "artifact does NOT contain substring",
            "provenance": "promptfoo-taxonomy",
        },
    },
    {
        "name": "regex",
        "tier": "L1",
        "kind": "artifact_text",
        "template": {
            "expect": "artifact matches regex pattern",
            "provenance": "promptfoo-taxonomy",
        },
    },
    {
        "name": "is-json",
        "tier": "L1",
        "kind": "artifact_text",
        "template": {
            "expect": "artifact is valid JSON",
            "provenance": "promptfoo-taxonomy",
        },
    },
    {
        "name": "json-schema",
        "tier": "L1",
        "kind": "artifact_text",
        "template": {
            "expect": "artifact matches JSON schema",
            "provenance": "promptfoo-taxonomy",
        },
    },
    {
        "name": "equals",
        "tier": "L1",
        "kind": "artifact_text",
        "template": {
            "expect": "artifact equals expected value",
            "provenance": "promptfoo-taxonomy",
        },
    },
    {
        "name": "starts-with",
        "tier": "L1",
        "kind": "artifact_text",
        "template": {
            "expect": "artifact starts with expected prefix",
            "provenance": "promptfoo-taxonomy",
        },
    },
    {
        "name": "exec",
        "tier": "L1",
        "kind": "shell",
        "template": {
            "expect": "custom script exits 0",
            "provenance": "promptfoo-taxonomy",
        },
    },
    {
        "name": "file-exists",
        "tier": "L1",
        "kind": "file_exists",
        "template": {
            "expect": "artifact file exists",
            "provenance": "promptfoo-taxonomy",
        },
    },
]


def main() -> int:
    with get_conn() as c:
        existing = {
            row["name"]
            for row in c.execute("SELECT name FROM check_templates").fetchall()
        }

        inserted = 0
        skipped = 0
        for preset in PROMPTFOO_PRESETS:
            if preset["name"] in existing:
                print(f"  SKIP  {preset['name']} — already exists")
                skipped += 1
                continue

            c.execute(
                "INSERT INTO check_templates (name, tier, kind, template) VALUES (%s, %s, %s, %s)",
                (
                    preset["name"],
                    preset["tier"],
                    preset["kind"],
                    json.dumps(preset["template"]),
                ),
            )
            print(f"  INSERT {preset['name']}  kind={preset['kind']}  provenance=promptfoo-taxonomy")
            inserted += 1

        print(f"\nDone: {inserted} inserted, {skipped} skipped, {inserted + skipped} total")

    # Verify in a separate connection
    with get_conn() as c:
        rows = c.execute(
            "SELECT name, kind, template->>'provenance' AS prov FROM check_templates ORDER BY name"
        ).fetchall()
        print("\nCurrent check_templates:")
        for r in rows:
            print(f"  {r['name']:20s}  kind={r['kind']:15s}  provenance={r['prov']}")

        return 0


if __name__ == "__main__":
    sys.exit(main())
