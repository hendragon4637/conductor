#!/usr/bin/env python3
"""File 03.6: flag any ``done`` node_session with a NULL ``goal_review``.

Exit 0 if all done node_sessions have a non-null goal_review.
Exit 1 (and print offenders) if any are missing.
"""
from __future__ import annotations

import os
import sys

import psycopg


DB_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://aipc:aipc@127.0.0.1:5432/aipc_conductor",
)


def main() -> int:
    try:
        with psycopg.connect(DB_URL, row_factory=psycopg.rows.dict_row) as c:
            with c.cursor() as cur:
                cur.execute(
                    """SELECT id, run_id, node_id, verdict, goal_review, commit_tag, finished_at
                       FROM node_sessions
                       WHERE verdict = 'done'
                         AND goal_review IS NULL
                       ORDER BY finished_at DESC
                    """
                )
                offenders = cur.fetchall()
    except Exception as e:
        print(f"[SCORE_SANITY] DB error: {e}", file=sys.stderr)
        return 2

    if not offenders:
        print("[SCORE_SANITY] OK — all done node_sessions have non-null goal_review.")
        return 0

    print(f"[SCORE_SANITY] FAIL — {len(offenders)} done node_session(s) with NULL goal_review:")
    for o in offenders:
        print(
            f"  id={o['id']} run_id={o['run_id']} node_id={o['node_id']} "
            f"verdict={o['verdict']} commit_tag={o['commit_tag']} "
            f"finished_at={o['finished_at']}"
        )
    return 1


if __name__ == "__main__":
    sys.exit(main())
