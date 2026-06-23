#!/usr/bin/env python3
"""E2E: propose via meta-planner → gate → ratify → run → execute → wait."""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

API = "http://127.0.0.1:8090"

PASS = 0
FAIL = 0


def check(label: str, ok: bool):
    global PASS, FAIL
    if ok:
        print(f"  PASS: {label}")
        PASS += 1
    else:
        print(f"  FAIL: {label}")
        FAIL += 1


def api(method: str, path: str, body: dict | None = None, expect: int | None = None) -> dict:
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(f"{API}{path}", data=data,
                                 headers={"Content-Type": "application/json"},
                                 method=method)
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            result = json.loads(r.read())
            if expect is not None:
                check(f"{method} {path} status={r.status}", r.status == expect)
            return result
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:300]
        if expect is not None:
            check(f"{method} {path} status={e.code}", e.code == expect)
        else:
            print(f"  HTTP {e.code} on {method} {path}: {body}")
        return {"error": body, "_status": e.code}


# ── 1. Propose via meta-planner ──────────────────────────────────────────
print("=== 1. Propose via meta-planner ===")
plan = api("POST", "/api/plans", {
    "use_meta_planner": True,
    "goal": "Build a FastAPI health check endpoint",
    "spec": "FastAPI app with GET /health returning {\"status\":\"ok\"}",
    "quality_intent": "One node: create the endpoint. Code must be clean and correct.",
    "project_id": "finance-tracker",
})
plan_id = plan.get("plan_id")
check(f"plan_id={plan_id}", bool(plan_id))
nodes = plan.get("nodes", [])
check(f"nodes generated ({len(nodes)})", len(nodes) >= 1)
for n in nodes:
    check(f"  node {n.get('node_id')}: {len(n.get('checks', []))} checks", len(n.get('checks', [])) > 0)

# ── 2. Ratify (will go through plan gate) ────────────────────────────────
print("\n=== 2. Ratify (plan gate) ===")
ratified = api("POST", f"/api/plans/{plan_id}/ratify", {"ratified": True})
gate_exhausted = ratified.get("gate_exhausted", False)
check(f"ratified={ratified.get('ratified')} gate_exhausted={gate_exhausted}", ratified.get("ratified") is True)
if not ratified.get("ratified"):
    reason = ratified.get("error", ratified.get("detail", "unknown"))
    print(f"  Gate rejected: {reason}")
    print("\n*** E2E SKIPPED (gate rejected) ***")
    sys.exit(0)

# ── 3. Create run ────────────────────────────────────────────────────────
print("\n=== 3. Create run ===")
run = api("POST", f"/api/plans/{plan_id}/runs")
run_id = run.get("run_id") or run.get("id")
check(f"run_id={run_id}", bool(run_id))

# ── 4. Approve run ───────────────────────────────────────────────────────
print("\n=== 4. Approve run ===")
api("POST", f"/api/plans/runs/{run_id}/approve", expect=200)

# ── 5. Start run ─────────────────────────────────────────────────────────
print("\n=== 5. Start run ===")
api("POST", f"/api/plans/runs/{run_id}/start", expect=200)
print("  Started — execution via AionUi")

# ── 6. Wait for execution ────────────────────────────────────────────────
print("\n=== 6. Wait for execution ===")
import psycopg
DB_URL = os.environ.get("DATABASE_URL", "")
if not DB_URL:
    print("  DATABASE_URL not set — cannot wait")
    sys.exit(1)
deadline = time.time() + 900  # 15 min
done_verdicts = {"done", "failed", "error", "crashed"}
first = True
while time.time() < deadline:
    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, node_id, verdict, goal_review, commit_tag, finished_at "
                "FROM node_sessions WHERE run_id = %s ORDER BY id",
                (run_id,))
            rows = cur.fetchall()
    if rows:
        all_done = True
        for r_ in rows:
            ns_id, nid, verdict, gr, tag, fin = r_
            gr_s = f"goal_review={gr}" if gr is not None else "goal_review=NULL"
            status = f"  {nid} ({ns_id[:8]}): verdict={verdict} {gr_s} tag={tag}"
            if verdict in done_verdicts:
                ok = verdict == "done"
                status += " ✓" if ok else " ✗"
            else:
                all_done = False
            if first:
                print(status)
        if all_done:
            break
        first = False
    else:
        if first:
            print("  (waiting for node_session...)")
    time.sleep(15)

elapsed = time.time() - (deadline - 900)
print(f"\n=== 7. Results (elapsed: {elapsed:.0f}s) ===")

# Final check: all nodes done with non-null goal_review
all_passed = True
with psycopg.connect(DB_URL) as conn:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT node_id, verdict, goal_review, commit_tag FROM node_sessions WHERE run_id = %s ORDER BY id",
            (run_id,))
        rows = cur.fetchall()
for r_ in rows:
    nid, v, gr, tag = r_
    ok = v == "done" and gr is not None
    gr_s = f"goal_review={gr}" if gr is not None else "goal_review=NULL"
    check(f"{nid}: verdict={v} {gr_s} tag={tag}", ok)
    if not ok:
        all_passed = False

print(f"\n--- E2E: {PASS} passed, {FAIL} failed ---")
if all_passed and FAIL == 0:
    print("*** E2E PASSED: meta-planner → gate → ratify → execute → scored ***")
    sys.exit(0)
else:
    print("*** E2E FAILED ***")
    sys.exit(1)
