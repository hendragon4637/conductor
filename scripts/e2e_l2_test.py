#!/usr/bin/env python3
"""E2E: create plan → ratify → run → wait for done → verify goal_review (L2 scoring)."""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
import urllib.error

API = "http://127.0.0.1:8090"

def _api(method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(f"{API}{path}", data=data,
                                 headers={"Content-Type": "application/json"},
                                 method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        print(f"  HTTP {e.code} on {method} {path}: {e.read().decode()[:200]}")
        raise

# 1. Create plan
print("=== 1. Create plan ===")
plan = _api("POST", "/api/plans", {
    "user_intent": "Build a GET /health endpoint",
    "goal": "Create a health check returning {\"status\":\"ok\"} with FastAPI",
    "project_id": "finance-tracker",
    "spec": "FastAPI; GET /health returns {\"status\":\"ok\"}; .venv with no host installs",
    "quality_intent": "reject if endpoint does not return 200 OK from .venv",
    "backend": "opencode",
    "nodes": [{
        "id": "node-1",
        "members": [{"agent_config": "finance-fullstack-executor", "backend": "opencode"}],
        "task": {"text": "Create backend/main.py. GET /health returns {\"status\":\"ok\"}. Use FastAPI. Create .venv, pip install fastapi uvicorn httpx pytest INTO .venv."},
        "success": {"text": "Endpoint returns 200 OK from .venv"},
    }],
})
plan_id = plan.get("plan_id") or plan.get("id")
print(f"  Plan: {plan_id}  ({len(plan.get('nodes', []))} nodes)")

# 2. Ratify
print("=== 2. Ratify ===")
_api("POST", f"/api/plans/{plan_id}/ratify", {"ratified": True})
print("  Ratified")

# 3. Create run
print("=== 3. Create run ===")
run = _api("POST", f"/api/plans/{plan_id}/runs")
run_id = run.get("run_id") or run.get("id")
print(f"  Run: {run_id}")

# 4. Approve run
print("=== 4. Approve run ===")
_api("POST", f"/api/plans/runs/{run_id}/approve")

# 5. Start run
print("=== 5. Start run ===")
_api("POST", f"/api/plans/runs/{run_id}/start")
print("  Started — execution begins via AionUi")

# 6. Wait for node to complete (poll node_sessions)
print("\n=== 6. Wait for execution ===")
import psycopg
DB_URL = os.environ["DATABASE_URL"]
deadline = time.time() + 600  # 10 min max
done_verdicts = {"done", "failed", "error"}
while time.time() < deadline:
    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, node_id, verdict, goal_review, commit_tag, finished_at "
                "FROM node_sessions WHERE run_id = %s ORDER BY id",
                (run_id,))
            rows = cur.fetchall()
    if rows:
        for r in rows:
            nid, verdict, gr, tag = r[1], r[2], r[3], r[4]
            gr_str = f"goal_review={gr}" if gr is not None else "goal_review=NULL"
            print(f"  node={nid} verdict={verdict} {gr_str} tag={tag}", end="")
            if verdict in done_verdicts:
                print(" ✓" if verdict == "done" else " ✗")
            else:
                print()
        if any(r[2] in done_verdicts for r in rows):
            break
    else:
        print("  (waiting for node_session to appear...)")
    time.sleep(15)

elapsed = time.time() - (deadline - 600)
print(f"\n=== 7. Verify goal_review (elapsed: {elapsed:.0f}s) ===")

# Run score_sanity check
import subprocess
result = subprocess.run(
    [sys.executable, "scripts/score_sanity.py"],
    capture_output=True, text=True, timeout=30,
)
print(result.stdout.strip())
if result.returncode == 0:
    print("\n*** E2E PASSED: All done nodes have non-null goal_review (L2 scored!) ***")
    sys.exit(0)
else:
    print(f"\n*** E2E FAILED: {result.stdout.strip()} ***")
    sys.exit(1)
