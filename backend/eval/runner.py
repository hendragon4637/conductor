"""
Eval runner — runs deterministic + judge tracks for one trace.

Usage:
  python -m backend.eval.runner --trace-id <uuid>
  python -m backend.eval.runner --recent 10
"""
from __future__ import annotations
import argparse
import sys
from uuid import UUID

from backend.db import queries
from backend.eval.deterministic import score_deterministic
from backend.eval.judge import score_judge


SCORES_TABLE_SQL = """
INSERT INTO scores (trace_id, track, dimension, value, clauses_violated, judge_metadata)
VALUES (%s, %s, %s, %s, %s, %s::jsonb)
"""


def run_for_trace(trace_id: UUID) -> dict:
    summary = {"trace_id": str(trace_id), "deterministic": None, "judge": None, "errors": []}

    with queries.conn() as c, c.cursor() as cur:
        cur.execute(
            "SELECT t.*, ta.user_intent FROM traces t JOIN tasks ta ON ta.task_id = t.task_id WHERE t.trace_id = %s",
            (str(trace_id),),
        )
        trace = cur.fetchone()
        if not trace:
            summary["errors"].append("trace not found")
            return summary

    if trace["status"] not in ("complete", "failed"):
        summary["errors"].append(f"trace not in terminal state: {trace['status']}")
        return summary

    # Deterministic track
    try:
        det = score_deterministic(trace)
        summary["deterministic"] = det
        _persist_score(trace_id, "deterministic", det["dimension"], det["value"], det.get("clauses_violated"))
    except Exception as e:
        summary["errors"].append(f"deterministic failed: {e}")

    # Judge track
    try:
        jd = score_judge(trace)
        summary["judge"] = jd
        _persist_score(trace_id, "judge", jd["dimension"], jd["value"], jd.get("clauses_violated"), judge_metadata=jd.get("metadata"))
    except Exception as e:
        summary["errors"].append(f"judge failed: {e}")

    return summary


def _persist_score(trace_id, track: str, dimension: str, value: float,
                   clauses_violated: list | None = None,
                   judge_metadata: dict | None = None) -> None:
    import json
    with queries.conn() as c, c.cursor() as cur:
        cur.execute(SCORES_TABLE_SQL,
                    (str(trace_id), track, dimension, value, clauses_violated or None,
                     json.dumps(judge_metadata) if judge_metadata else None))
        c.commit()


def main():
    p = argparse.ArgumentParser(description="Eval runner — deterministic + judge tracks")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--trace-id", help="UUID of a single trace to evaluate")
    g.add_argument("--recent", type=int, help="evaluate N most-recent complete/failed traces")
    args = p.parse_args()

    trace_ids = []
    if args.trace_id:
        trace_ids.append(UUID(args.trace_id))
    else:
        with queries.conn() as c, c.cursor() as cur:
            cur.execute(
                """SELECT trace_id FROM traces
                   WHERE status IN ('complete','failed')
                   ORDER BY ended_at DESC NULLS LAST LIMIT %s""",
                (args.recent,),
            )
            trace_ids = [
                UUID(str(r["trace_id"])) if not isinstance(r["trace_id"], UUID) else r["trace_id"]
                for r in cur.fetchall()
            ]

    for tid in trace_ids:
        s = run_for_trace(tid)
        print(s)


if __name__ == "__main__":
    main()
