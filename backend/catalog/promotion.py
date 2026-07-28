"""Catalog promotion — human-only vetted tier promotion with RLS guard.

The tool_catalog table uses RLS to prevent agents from setting status='vetted'
directly. This module provides the human-facing promotion endpoint and the
agent-facing proposal mechanism.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

import psycopg

logger = logging.getLogger(__name__)


def get_db_url() -> str:
    return os.environ["DATABASE_URL"]


def promote(id: str, db_url: str | None = None) -> dict[str, Any]:
    """Human promotion endpoint logic — upgrades a candidate to vetted.

    This must be called from a route that authenticates the caller as human
    (e.g., POST /catalog/{id}/promote with human session).

    Args:
        id: UUID of the tool_catalog entry to promote.
        db_url: Database URL (defaults to DATABASE_URL env var).

    Returns:
        Dict with promoted tool info.
    """
    _db_url = db_url or get_db_url()

    with psycopg.connect(_db_url) as conn, conn.cursor() as cur:
        # The RLS policy for the agent role blocks UPDATE on vetted rows.
        # Human sessions bypass RLS via session variable or separate role.
        cur.execute(
            """UPDATE tool_catalog
                  SET status = 'vetted',
                      status_by = 'human',
                      status_changed_at = %s,
                      updated_at = %s
                WHERE id = %s AND status = 'candidate'
                RETURNING id, name, kind""",
            (datetime.now(timezone.utc), datetime.now(timezone.utc), id),
        )
        row = cur.fetchone()
        conn.commit()

    if row:
        return {"status": "promoted", "id": row[0], "name": row[1], "kind": row[2]}
    return {"status": "not_found", "id": id}


def list_candidates(db_url: str | None = None) -> list[dict[str, Any]]:
    """List all candidate-tier tools awaiting human promotion."""
    _db_url = db_url or get_db_url()
    results = []
    with psycopg.connect(_db_url) as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT id, name, description, kind, source_url, stars, metadata->>'source' as source
                 FROM tool_catalog
                WHERE status = 'candidate'
                ORDER BY stars DESC, created_at DESC"""
        )
        for row in cur.fetchall():
            results.append({
                "id": str(row[0]),
                "name": row[1],
                "description": row[2],
                "kind": row[3],
                "source_url": row[4],
                "stars": row[5],
                "source": row[6] or "unknown",
            })
    return results


def list_vetted(db_url: str | None = None) -> list[dict[str, Any]]:
    """List all vetted-tier tools (available for install)."""
    _db_url = db_url or get_db_url()
    results = []
    with psycopg.connect(_db_url) as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT id, name, description, kind, source_url, license, stars, maturity_score
                 FROM tool_catalog
                WHERE status = 'vetted'
                ORDER BY name"""
        )
        for row in cur.fetchall():
            results.append({
                "id": str(row[0]),
                "name": row[1],
                "description": row[2],
                "kind": row[3],
                "source_url": row[4],
                "license": row[5],
                "stars": row[6],
                "maturity_score": row[7],
            })
    return results
