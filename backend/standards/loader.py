"""Load and query domain_standards from the database."""

from __future__ import annotations

import logging
import os
from typing import Any

import psycopg

logger = logging.getLogger(__name__)


def get_db_url() -> str:
    return os.environ["DATABASE_URL"]


def get_standard(slug: str) -> dict[str, Any] | None:
    """Load a domain standard by slug."""
    with psycopg.connect(get_db_url()) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT slug, name, kind, conventions_md, tool_manifest, artifact_spec, scaffold_tree, source_repo, version, families"
            " FROM domain_standards WHERE slug = %s",
            (slug,),
        )
        row = cur.fetchone()
    if not row:
        return None
    return {
        "slug": row[0],
        "name": row[1],
        "kind": row[2],
        "conventions_md": row[3] or "",
        "tool_manifest": row[4] if isinstance(row[4], (list, dict)) else [],
        "artifact_spec": row[5] if isinstance(row[5], dict) else {},
        "scaffold_tree": row[6] if isinstance(row[6], list) else [],
        "source_repo": row[7],
        "version": row[8],
        "families": row[9] if isinstance(row[9], list) else [],
    }


def list_standards(kind: str | None = None) -> list[dict[str, Any]]:
    """List all domain standards, optionally filtered by kind."""
    query = "SELECT slug, name, kind, version FROM domain_standards"
    params = []
    if kind:
        query += " WHERE kind = %s"
        params.append(kind)
    query += " ORDER BY name"

    results = []
    with psycopg.connect(get_db_url()) as conn, conn.cursor() as cur:
        cur.execute(query, params)
        for row in cur.fetchall():
            results.append({
                "slug": row[0],
                "name": row[1],
                "kind": row[2],
                "version": row[3],
            })
    return results


def get_capability_standard(capability_name: str) -> str | None:
    """Get the standard_id (slug) for a capability, if linked."""
    with psycopg.connect(get_db_url()) as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT ds.slug FROM domain_standards ds
                JOIN capabilities c ON c.standard_id = ds.id
               WHERE c.name = %s""",
            (capability_name,),
        )
        row = cur.fetchone()
    return row[0] if row else None


def get_run_standards(run_id: str) -> list[str]:
    """Get the standard slugs associated with a run."""
    with psycopg.connect(get_db_url()) as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT ds.slug FROM domain_standards ds
                JOIN runs r ON r.standard_ids @> ARRAY[ds.id]
               WHERE r.id = %s""",
            (run_id,),
        )
        return [row[0] for row in cur.fetchall()]


def list_standard_menu(exclude_planning: bool = True) -> list[dict[str, Any]]:
    """Return menu data from domain_standards for the formulation prompt.

    Returns slug, name, selector_blurb, families, default_subdir and
    delivery_form (from ``artifact_spec->'delivery_spec'->>'form'``) for
    every active domain standard. Excludes 'planning' kind by default. The
    menu drives the LLM's choice of standard_ids in the multi-component flow.

    Returns:
        List of dicts with keys: slug, name, blurb, families, default_subdir,
        delivery_form
    """
    results = []
    with psycopg.connect(get_db_url()) as conn, conn.cursor() as cur:
        if exclude_planning:
            cur.execute(
                """SELECT slug, name, selector_blurb, families, default_subdir,
                          artifact_spec->'delivery_spec'->>'form' AS delivery_form
                     FROM domain_standards
                    WHERE kind = 'domain' AND active
                    ORDER BY name"""
            )
        else:
            cur.execute(
                """SELECT slug, name, selector_blurb, families, default_subdir,
                          artifact_spec->'delivery_spec'->>'form' AS delivery_form
                     FROM domain_standards
                    WHERE active
                    ORDER BY name"""
            )
        for row in cur.fetchall():
            results.append({
                "slug": row[0],
                "name": row[1],
                "blurb": row[2] or "",
                "families": row[3] if isinstance(row[3], list) else [],
                "default_subdir": row[4] or "",
                "delivery_form": row[5] or "",
            })
    return results
