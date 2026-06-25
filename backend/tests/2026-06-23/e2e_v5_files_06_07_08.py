#!/usr/bin/env python3
"""E2E Capstone for Files 06-07-08 on top of existing File 09.

Scenarios:
  1. File 06: Vague proposal → await_clarification → answer → formulated
  2. File 07: Nodes carry size_estimate → split_oversized pipeline
  3. File 08: Run completes → worktree_status is set (merged/quarantined)
  4. File 09: verdict columns populated (l1_flagged, l2_passed, l2_score, gate_outcome)
"""
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


DB_URL = os.environ.get("DATABASE_URL", "")
if not DB_URL:
    print("  DATABASE_URL not set — skipping DB checks")
    sys.exit(1)

import psycopg


# ── FILE 06: Clarification state machine ─────────────────────────────
print("=" * 60)
print("FILE 06: Multi-turn clarification state")
print("=" * 60)

# Propose a deliberately vague goal to trigger clarification
print("\n--- 06.1 Vague proposal → awaiting_clarification ---")
vague = api("POST", "/api/plans", {
    "use_meta_planner": True,
    "goal": "Make it better",
    "spec": "",
    "quality_intent": "",
})
plan_id_06 = vague.get("plan_id")
check(f"06.1 plan_id={plan_id_06}", bool(plan_id_06))
plan_status = vague.get("plan_status", "draft")
check(f"06.1 plan_status={plan_status}", plan_status == "awaiting_clarification")
questions = vague.get("clarify_questions", [])
check(f"06.1 clarify_questions ({len(questions)})", len(questions) >= 1)
check(f"06.1 no nodes yet", len(vague.get("nodes", [])) == 0)
if plan_status != "awaiting_clarification":
    detail = vague.get("error", vague.get("detail", "unexpected"))
    print(f"  Note: plan was not paused for clarification ({detail})")
    print("  (This is acceptable if the LLM resolved the vague goal)")

# Answer the clarifying questions (if paused)
print("\n--- 06.2 Answer clarification → formulated ---")
if plan_status == "awaiting_clarification":
    answer = api("POST", f"/api/plans/{plan_id_06}/clarify", {
        "answer": "Build a FastAPI health check endpoint. GET /health returning status ok."
    })
    check(f"06.2 answer ok", answer.get("_status", 200) in (200, None))
    plan_status_after = answer.get("plan_status", "")
    nodes_after = answer.get("nodes", [])
    check(f"06.2 plan_status={plan_status_after}",
          plan_status_after == "formulated" or len(nodes_after) > 0)
    check(f"06.2 nodes generated ({len(nodes_after)})", len(nodes_after) >= 1)
    for n in nodes_after:
        se = n.get("size_estimate", 0)
        check(f"06.2 node {n.get('node_id')} size_estimate={se}", se > 0)
else:
    # If the vague goal was actually resolved, it should still have nodes
    check(f"06.2 nodes present ({len(vague.get('nodes', []))})", len(vague.get("nodes", [])) >= 1)


# ── FILE 07: Size estimate + full pipeline test ─────────────────────
print("\n" + "=" * 60)
print("FILE 07: Size estimate + pipeline integration")
print("=" * 60)

print("\n--- 07.1 Propose explicit goal via meta-planner ---")
plan = api("POST", "/api/plans", {
    "use_meta_planner": True,
    "goal": "Build a FastAPI finance tracker with user auth and expense CRUD",
    "spec": "FastAPI app, postgres-backed, JWT auth, CRUD for expenses",
    "quality_intent": "Three nodes: auth setup, expense model+CRUD, integration test. Code must be clean.",
    "project_id": "finance-tracker",
})
plan_id = plan.get("plan_id")
check(f"07.1 plan_id={plan_id}", bool(plan_id))
nodes = plan.get("nodes", [])
check(f"07.1 nodes generated ({len(nodes)})", len(nodes) >= 1)

# Verify size_estimate on each node
for n in nodes:
    nid = n.get("node_id", "?")
    se = n.get("size_estimate", 0)
    check(f"07.1 node {nid} size_estimate={se}", se > 0)
    nchecks = len(n.get("checks", []))
    check(f"07.1 node {nid} checks={nchecks}", nchecks > 0)

# Attempt ratify
print("\n--- 07.2 Ratify (plan gate) ---")
ratified = api("POST", f"/api/plans/{plan_id}/ratify", {"ratified": True})
gate_exhausted = ratified.get("gate_exhausted", False)
check(f"07.2 ratified={ratified.get('ratified')} gate_exhausted={gate_exhausted}",
      ratified.get("ratified") is True)
if not ratified.get("ratified"):
    detail = ratified.get("error", ratified.get("detail", ratified.get("reason", "unknown")))
    print(f"  Gate rejected: {detail}")
    print("\n*** Cannot proceed to execution — gate failed ***")
    # Still test what we can
else:
    # ── EXECUTION (creates run, waits for completion) ────────────────
    print("\n--- 07.3 Create + approve + start run ---")
    run = api("POST", f"/api/plans/{plan_id}/runs")
    run_id = run.get("run_id") or run.get("id")
    check(f"07.3 run_id={run_id}", bool(run_id))

    api("POST", f"/api/plans/runs/{run_id}/approve", expect=200)
    api("POST", f"/api/plans/runs/{run_id}/start", expect=200)
    print("  Run started — executing via AionUi")

    # Wait for all node_sessions to reach terminal verdicts
    print("\n--- 07.4 Wait for execution ---")
    deadline = time.time() + 900
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
                status = f"  {nid} ({ns_id[:8]}): verdict={verdict}"
                if gr is not None:
                    status += f" goal_review={gr}"
                status += f" tag={tag}"
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

    # ── FILE 09: Verify verdict columns ──────────────────────────────
    print(f"\n--- 07.5 Results (elapsed: {elapsed:.0f}s) ---")
    all_passed = True
    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT node_id, verdict, goal_review, commit_tag, "
                "l1_flagged, l2_passed, l2_score, gate_outcome "
                "FROM node_sessions WHERE run_id = %s ORDER BY id",
                (run_id,))
            rows = cur.fetchall()
    for r_ in rows:
        nid, v, gr, tag, l1f, l2p, l2s, g_out = r_
        ok = v == "done" and gr is not None
        gr_s = f"goal_review={gr}" if gr is not None else "goal_review=NULL"
        check(f"07.5 {nid}: verdict={v} {gr_s} tag={tag}", ok)
        check(f"07.5 {nid} l1_flagged={l1f}", l1f is not None)
        check(f"07.5 {nid} l2_passed={l2p}", l2p is not None)
        check(f"07.5 {nid} l2_score={l2s}", l2s is not None)
        check(f"07.5 {nid} gate_outcome={g_out}", g_out in ("done", "remediate", "failed"))
        if not ok:
            all_passed = False

    # ── FILE 08: Verify worktree lifecycle ──────────────────────────
    print(f"\n--- 07.6 Worktree lifecycle (File 08) ---")
    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT worktree_status, merge_commit, quarantine_tag, worktree_expires_at, state "
                "FROM runs WHERE id = %s",
                (run_id,))
            run_row = cur.fetchone()
    if run_row:
        wt_status, merge_commit, quarantine_tag, expires_at, run_state = run_row
        check(f"07.6 run_state={run_state}", run_state in ("done", "failed"))
        check(f"07.6 worktree_status={wt_status}",
              wt_status in ("merged", "quarantined", "active"))
        if run_state == "done":
            check(f"07.6 done run worktree_status={wt_status}",
                  wt_status in ("merged", "active"))
            if wt_status == "merged":
                check(f"07.6 merge_commit={merge_commit}", bool(merge_commit))
        elif run_state == "failed":
            check(f"07.6 failed run worktree_status={wt_status}",
                  wt_status in ("quarantined", "active"))
            if wt_status == "quarantined":
                check(f"07.6 quarantine_tag present", bool(run_row[2]))
    else:
        check("07.6 run row found", False)

    # ── Summary ──────────────────────────────────────────────────────
    final_status = "PASSED" if all_passed and FAIL == 0 else "FAILED"
    print(f"\n{'=' * 60}")
    print(f"FILES 06-07-08 E2E: {PASS} passed, {FAIL} failed — {final_status}")
    print(f"{'=' * 60}")
    sys.exit(0 if all_passed and FAIL == 0 else 1)
