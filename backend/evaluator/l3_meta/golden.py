from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from typing import Any


@dataclass
class GoldenItem:
    node_type: str
    artifact_ref: str
    rubric_item: str
    human_label: bool
    expected_score: float | None = None
    item_id: str = ""
    frozen: bool = True


def _db_url() -> str:
    return os.environ.get("DATABASE_URL", "")


def load_golden(node_type: str, limit: int = 100) -> list[GoldenItem]:
    """Load frozen human-labeled golden items for a node type.

    Args:
        node_type: e.g. 'build', 'test', 'review', 'design', 'default'.
        limit: Maximum items to return.

    Returns:
        List of GoldenItem from the golden_set table.
        Returns empty list if DB is unreachable.
    """
    url = _db_url()
    if not url:
        return []
    try:
        import psycopg
        from psycopg.rows import dict_row

        with psycopg.connect(url, row_factory=dict_row) as c:
            with c.cursor() as cur:
                cur.execute(
                    """SELECT id, node_type, artifact_ref, rubric_item,
                              human_label, expected_score, frozen
                       FROM golden_set
                      WHERE node_type = %s AND frozen = TRUE
                      ORDER BY created_at DESC
                      LIMIT %s""",
                    (node_type, limit),
                )
                rows = cur.fetchall()
    except Exception:
        return []

    return [
        GoldenItem(
            item_id=str(r["id"]),
            node_type=r["node_type"],
            artifact_ref=r["artifact_ref"],
            rubric_item=r["rubric_item"],
            human_label=bool(r["human_label"]),
            expected_score=float(r["expected_score"]) if r.get("expected_score") else None,
            frozen=bool(r["frozen"]),
        )
        for r in rows
    ]


def add_golden(
    node_type: str,
    artifact_ref: str,
    rubric_item: str,
    human_label: bool,
    expected_score: float | None = None,
    labeled_by: str = "human",
) -> str:
    """Insert a new item into the golden set.

    IMPORTANT: This function is INTENDED for human use only.
    Nothing in the automated pipeline calls ``add_golden``.
    Doing so would close the calibration loop and destroy the anchor.

    Args:
        node_type: Node type this item belongs to.
        artifact_ref: Path or reference to the artifact file.
        rubric_item: The rubric question.
        human_label: Whether the criteria was met.
        expected_score: Optional continuous score.
        labeled_by: Who labeled this (default "human").

    Returns:
        The new item's UUID as a string, or empty string on failure.
    """
    url = _db_url()
    if not url:
        return ""
    item_id = str(uuid.uuid4())
    try:
        import psycopg

        with psycopg.connect(url) as c:
            with c.cursor() as cur:
                cur.execute(
                    """INSERT INTO golden_set
                       (id, node_type, artifact_ref, rubric_item,
                        human_label, expected_score, labeled_by, frozen)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, TRUE)""",
                    (item_id, node_type, artifact_ref, rubric_item,
                     human_label, str(expected_score) if expected_score is not None else None,
                     labeled_by),
                )
            c.commit()
    except Exception:
        return ""
    return item_id


def count_golden(node_type: str | None = None, split: str | None = None) -> int:
    """Count frozen golden items, optionally filtered by node_type and split.

    Args:
        node_type: If provided, only count items for this node type.
        split: If provided, only count items with this split value
               (e.g. 'calibration' or 'heldout'). Ignored when node_type is None.

    Returns:
        Item count, or 0 on error / no DB.
    """
    url = _db_url()
    if not url:
        return 0
    try:
        import psycopg

        with psycopg.connect(url) as c:
            with c.cursor() as cur:
                if node_type:
                    if split:
                        cur.execute(
                            "SELECT COUNT(*) FROM golden_set WHERE node_type = %s AND split = %s AND frozen = TRUE",
                            (node_type, split),
                        )
                    else:
                        cur.execute(
                            "SELECT COUNT(*) FROM golden_set WHERE node_type = %s AND frozen = TRUE",
                            (node_type,),
                        )
                else:
                    cur.execute(
                        "SELECT COUNT(*) FROM golden_set WHERE frozen = TRUE"
                    )
                row = cur.fetchone()
                return int(row[0]) if row else 0
    except Exception:
        return 0
