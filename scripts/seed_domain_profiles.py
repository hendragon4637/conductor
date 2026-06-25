#!/usr/bin/env python3
"""Seed domain profiles into the database.

Creates the ``domain_profiles`` table if it does not exist and upserts
the 6 starter profiles (software_app, cli_script, api_service,
data_pipeline, research_report, generic).

Usage:
    python scripts/seed_domain_profiles.py
"""
from __future__ import annotations

import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root))

from backend.db import get_connection
from backend.planning.domain_profile import seed_domain_profiles

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS domain_profiles (
    domain         TEXT PRIMARY KEY,
    acceptance     JSONB NOT NULL,
    conventions    JSONB NOT NULL DEFAULT '[]'::jsonb,
    custom         JSONB NOT NULL DEFAULT '{}'::jsonb,
    version        INTEGER NOT NULL DEFAULT 1,
    source         TEXT NOT NULL DEFAULT 'example-generated',
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


def main() -> None:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(CREATE_TABLE_SQL)
        conn.commit()
        cur.close()
        print("[seed] domain_profiles table ready")

        seed_domain_profiles(conn)
        print("[seed] domain profiles seeded successfully")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
