#!/usr/bin/env python3
"""Seed the tool_catalog table with existing skills and CLI capabilities as vetted entries.

This is a one-time seed script that:

1. Reads all skills with source='imported' or source='hand' from the ``skills`` table
   and inserts them into ``tool_catalog`` as ``kind='skill'`` entries with
   ``status='vetted'``, ``status_by='human'``, and ``maturity_score=1.0``.

2. Reads distinct capability names from ``agent_configs.new_capabilities`` (a JSONB
   array of strings) and inserts each as a ``kind='cli'`` entry in the catalog.

The script is idempotent — it uses ``ON CONFLICT (name) WHERE status <> 'retired'
DO NOTHING`` so rows that already exist (under an active status) are skipped.

Usage::

    # Default — uses DATABASE_URL env var
    uv run python scripts/seed_catalog_vetted.py

    # Explicit URL
    uv run python scripts/seed_catalog_vetted.py --db-url postgresql://user:pass@host/db
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from typing import Any

import psycopg

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)


# ── helpers ─────────────────────────────────────────────────────────────────


def get_db_url(args: argparse.Namespace) -> str:
    """Resolve database URL from --db-url argument or DATABASE_URL env var."""
    if args.db_url:
        return args.db_url
    url = os.environ.get("DATABASE_URL")
    if not url:
        logger.error("No --db-url provided and DATABASE_URL is not set")
        sys.exit(1)
    return url


def strip_markdown(text: str) -> str:
    """Remove common markdown formatting, returning plain text."""
    # Remove fenced code blocks (including the content)
    text = re.sub(r"```[\s\S]*?```", "", text)
    # Remove inline code markers
    text = re.sub(r"`([^`]+)`", r"\1", text)
    # Remove images, keep alt text
    text = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", text)
    # Remove links, keep label text
    text = re.sub(r"\[([^\]]*)\]\([^)]+\)", r"\1", text)
    # Remove heading markers
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    # Remove bold/italic markers
    text = re.sub(r"[*_]{1,3}([^*_]+)[*_]{1,3}", r"\1", text)
    # Remove horizontal rules
    text = re.sub(r"^-{3,}\s*$", "", text, flags=re.MULTILINE)
    # Remove blockquote markers
    text = re.sub(r"^>\s+", "", text, flags=re.MULTILINE)
    # Remove unordered list markers
    text = re.sub(r"^[\s]*[-*+]\s+", "", text, flags=re.MULTILINE)
    # Remove ordered list markers
    text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.MULTILINE)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def extract_url(body: str, name: str) -> str:
    """Extract the first http(s) URL found in *body*, or return a sensible default."""
    match = re.search(r"https?://[^\s)\]]+", body)
    if match:
        return match.group(0).rstrip(".,;:()")
    return f"https://github.com/anthropics/{name}"


# ── seed steps ──────────────────────────────────────────────────────────────


def seed_skills(conn: psycopg.Connection) -> tuple[int, int]:
    """Insert skills (source = 'imported' | 'hand') into tool_catalog as vetted.

    Returns (inserted, skipped).
    """
    inserted = 0
    skipped = 0

    with conn.cursor() as cur:
        cur.execute(
            """SELECT name, body, source, has_scripts
                 FROM skills
                WHERE source IN ('imported', 'hand')
                ORDER BY name"""
        )
        rows = cur.fetchall()

    total = len(rows)
    logger.info("Found %d skills to process", total)

    with conn.cursor() as cur:
        for idx, (name, body, source, has_scripts) in enumerate(rows, start=1):
            clean = strip_markdown(body)
            description = clean[:200].rstrip()
            # Fallback if stripping yields nothing meaningful
            if not description:
                description = f"Skill: {name}"

            source_url = extract_url(body, name)
            metadata: dict[str, Any] = {
                "source": source,
                "has_scripts": bool(has_scripts),
            }

            try:
                cur.execute(
                    """INSERT INTO tool_catalog
                           (name, description, kind, source_url, license,
                            stars, maturity_score, status, status_by, metadata)
                       VALUES (%s, %s, 'skill', %s, 'unknown',
                               0, 1.0, 'vetted', 'human', %s)
                       ON CONFLICT (name) WHERE status <> 'retired' DO NOTHING""",
                    (name, description, source_url, json.dumps(metadata)),
                )
                if cur.rowcount and cur.rowcount > 0:
                    inserted += 1
                else:
                    skipped += 1
            except Exception as exc:
                logger.warning("Error inserting skill '%s': %s", name, exc)
                skipped += 1

            # Progress log every 50 entries
            if idx % 50 == 0 or idx == total:
                logger.info(
                    "  skills progress: %d/%d — %d inserted, %d skipped",
                    idx,
                    total,
                    inserted,
                    skipped,
                )

    return inserted, skipped


def seed_cli_from_capabilities(conn: psycopg.Connection) -> tuple[int, int]:
    """Read distinct capability names from agent_configs and insert as CLI entries.

    Returns (inserted, skipped).
    """
    inserted = 0
    skipped = 0

    with conn.cursor() as cur:
        cur.execute(
            """SELECT DISTINCT jsonb_array_elements_text(new_capabilities) AS cap
                 FROM agent_configs
                WHERE new_capabilities IS NOT NULL
                  AND jsonb_array_length(new_capabilities) > 0
                ORDER BY cap"""
        )
        rows = cur.fetchall()

    total = len(rows)
    logger.info("Found %d distinct capability names from agent_configs", total)

    if total == 0:
        return 0, 0

    with conn.cursor() as cur:
        for (cap_name,) in rows:
            if not cap_name or not cap_name.strip():
                skipped += 1
                continue

            description = f"CLI tool: {cap_name}"
            source_url = f"https://github.com/anthropics/{cap_name}"

            try:
                cur.execute(
                    """INSERT INTO tool_catalog
                           (name, description, kind, source_url, license,
                            stars, maturity_score, status, status_by, metadata)
                       VALUES (%s, %s, 'cli', %s, 'unknown',
                               0, 1.0, 'vetted', 'human', %s)
                       ON CONFLICT (name) WHERE status <> 'retired' DO NOTHING""",
                    (cap_name, description, source_url, json.dumps({"source": "agent_configs"})),
                )
                if cur.rowcount and cur.rowcount > 0:
                    inserted += 1
                else:
                    skipped += 1
            except Exception as exc:
                logger.warning("Error inserting CLI entry '%s': %s", cap_name, exc)
                skipped += 1

    return inserted, skipped


# ── main ────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed tool_catalog with existing skills and CLI capabilities as vetted entries",
    )
    parser.add_argument(
        "--db-url",
        help="PostgreSQL connection URL (default: DATABASE_URL env var)",
        default=None,
    )
    args = parser.parse_args()

    db_url = get_db_url(args)

    logger.info("Connecting to database …")
    with psycopg.connect(db_url) as conn:
        sk_ins, sk_skp = seed_skills(conn)
        logger.info("Skills: %d inserted, %d skipped", sk_ins, sk_skp)

        cli_ins, cli_skp = seed_cli_from_capabilities(conn)
        logger.info("CLI entries: %d inserted, %d skipped", cli_ins, cli_skp)

        conn.commit()

    total_ins = sk_ins + cli_ins
    total_skp = sk_skp + cli_skp
    logger.info(
        "Done — %d total inserted, %d total skipped",
        total_ins,
        total_skp,
    )


if __name__ == "__main__":
    main()
