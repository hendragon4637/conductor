#!/usr/bin/env python3
"""File 06 — H1 full E2E test: create → ratify → run → approve → start → wait → verify.

H1 scenario: finance tracker via meta_planner (generates multi-node DAG).
Full execution of all nodes, then verify L2 scoring.

Usage:
    uv run python backend/tests/2026-06-26/e2e_h1_full.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

import psycopg

_API = "http://127.0.0.1:8090"
_DB_URL = os.environ.get("DATABASE_URL", "")
_POLL_INTERVAL = 15
_MAX_WAIT = 900
_DONE_VERDICTS = {"done", "failed", "error"}


def api(method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(
        f"{_API}{path}", data=data,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body_text = e.read().decode()[:300]
        print(f"  HTTP {e.code} on {method} {path}: {body_text}", flush=True)
        return {"error": body_text, "_status": e.code}
    except Exception as e:
        print(f"  Error on {method} {path}: {e}", flush=True)
        return {"error": str(e), "_status": 0}


def wait_for_nodes(run_id: str) -> list[tuple]:
    """Poll node_sessions until all nodes reach a terminal verdict."""
    deadline = time.time() + _MAX_WAIT
    last_print = 0

    while time.time() < deadline:
        try:
            with psycopg.connect(_DB_URL) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT id, node_id, verdict, goal_review, commit_tag, finished_at "
                        "FROM node_sessions WHERE run_id = %s ORDER BY id",
                        (run_id,),
                    )
                    rows = cur.fetchall()
        except Exception as e:
            print(f"  DB poll error: {e}", flush=True)
            rows = []

        now = time.time()
        if rows and (now - last_print >= 15 or not last_print):
            print(f"  Nodes: {len(rows)}", flush=True)
            for r in rows:
                vid, nid, verdict, gr, tag = r[0], r[1], r[2], r[3], r[4]
                gr_str = f"gr={gr}" if gr is not None else "gr=NULL"
                status = " ✓" if verdict == "done" else (" ✗" if verdict in {"failed", "error"} else "")
                print(f"    [{vid}] {nid} verdict={verdict} {gr_str} tag={tag}{status}", flush=True)
            last_print = now

        if rows and all(r[2] in _DONE_VERDICTS for r in rows):
            return rows

        time.sleep(_POLL_INTERVAL)

    print(f"\n  TIMEOUT after {_MAX_WAIT}s", flush=True)
    return rows


def main() -> int:
    pass_count = 0
    fail_count = 0

    def check(label: str, ok: bool):
        nonlocal pass_count, fail_count
        if ok:
            print(f"  PASS: {label}", flush=True)
            pass_count += 1
        else:
            print(f"  FAIL: {label}", flush=True)
            fail_count += 1

    print("=" * 60, flush=True)
    print("H1 Full E2E: Finance Tracker (meta_planner + full execution)", flush=True)
    print("=" * 60, flush=True)

    if not _DB_URL:
        print("  SKIP: DATABASE_URL not set", flush=True)
        return 0

    health = api("GET", "/health")
    check("backend reachable", health.get("status") == "ok")
    if health.get("status") != "ok":
        return 1

    print("\n--- Step 1: Create plan (H1 meta_planner) ---", flush=True)
    plan = api("POST", "/api/plans", {
        "use_meta_planner": True,
        "goal": "Build a finance tracker with FastAPI backend, add/list/delete expenses",
        "spec": "",
        "quality_intent": "",
    })
    plan_id = plan.get("plan_id") or plan.get("id")
    nodes = plan.get("nodes", [])
    check("plan created with plan_id", bool(plan_id))
    check("plan has nodes", len(nodes) >= 1)
    print(f"  plan_id={plan_id}  nodes={len(nodes)}", flush=True)
    for n in nodes:
        print(f"    node {n.get('id')}: {n.get('task', {}).get('text', '')[:80]}", flush=True)

    spec = plan.get("spec", "")
    conventions_injected = "end-to-end" in spec.lower() or "FE" in spec or "frontend" in spec.lower()
    check("FE convention injected by domain profile", conventions_injected)

    print("\n--- Step 2: Ratify plan ---", flush=True)
    ratify = api("POST", f"/api/plans/{plan_id}/ratify", {"ratified": True})
    check("plan ratified", ratify.get("_status", 200) != 400)

    print("\n--- Step 3: Create run ---", flush=True)
    run = api("POST", f"/api/plans/{plan_id}/runs")
    run_id = run.get("run_id") or run.get("id")
    check("run created", bool(run_id))
    print(f"  run_id={run_id}", flush=True)

    print("\n--- Step 4: Approve run ---", flush=True)
    approve = api("POST", f"/api/plans/runs/{run_id}/approve")
    check("run approved", approve.get("_status", 200) != 400)

    print("\n--- Step 5: Start run ---", flush=True)
    start = api("POST", f"/api/plans/runs/{run_id}/start")
    check("run started", start.get("_status", 200) != 400)
    print("  Execution triggered", flush=True)

    print("\n--- Step 6: Wait for execution ---", flush=True)
    t0 = time.time()
    node_rows = wait_for_nodes(run_id)
    elapsed = time.time() - t0

    print(f"\n  Elapsed: {elapsed:.0f}s | Nodes: {len(node_rows)}", flush=True)

    if not node_rows:
        check("at least one node_session created", False)
    else:
        check("node_sessions exist", True)
        for r in node_rows:
            vid, nid, verdict, gr, tag = r[0], r[1], r[2], r[3], r[4]
            ok = verdict == "done"
            check(f"{nid}: verdict={verdict}", ok)
            if verdict == "done":
                check(f"{nid}: goal_review scored", gr is not None)

        all_done = all(r[2] == "done" for r in node_rows)
        check("ALL nodes done", all_done)

    print("\n--- Step 7: Verify L2 scoring (score_sanity) ---", flush=True)
    try:
        sr = subprocess.run(
            [sys.executable, "scripts/score_sanity.py"],
            capture_output=True, text=True, timeout=30,
            cwd=os.path.join(os.path.dirname(__file__), "..", "..", ".."),
        )
        print(f"  {sr.stdout.strip()}", flush=True)
        check("score_sanity: all done nodes have non-null goal_review", sr.returncode == 0)
    except Exception as e:
        print(f"  score_sanity error: {e}", flush=True)
        check("score_sanity ran", False)

    print(f"\n{'=' * 60}", flush=True)
    print(f"Results: {pass_count} PASS / {fail_count} FAIL", flush=True)
    print(f"{'=' * 60}", flush=True)
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
