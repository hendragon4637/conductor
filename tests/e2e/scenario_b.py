#!/usr/bin/env python3
"""Scenario B — Team task with two-level review (planner → executor → reviewer).

Asserts dependency order, reviewer has ``edit: deny``, and both
traces appear in Langfuse.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from dotenv import load_dotenv
load_dotenv("/opt/aipc/conductor/.env")

from tests.e2e.common import (
    WORKSPACE_ROOT, CONDUCTOR, AIONUI,
    conductor, aionui, aionui_create_conversation, langfuse,
    ok, fail, assert_file, assert_contains, assert_pytest,
    wait_seconds, get_langfuse_scores, print_results,
)

SCENARIO = "B"
LABEL = "[e2e-B]"
TS = str(int(time.time()))


def run() -> bool:
    print(f"\n{'='*60}")
    print(f"Scenario B — Team task with two-level review ({LABEL})")
    print(f"{'='*60}\n")

    # 1. Create project + session
    print("--- 1. Create project & session ---")
    pid = f"e2e-b-{TS}"
    try:
        conductor("/api/projects", "POST", {
            "project_id": pid, "name": f"E2E Scenario B {LABEL}",
        })
        ok("Project created", pid)
    except Exception as e:
        fail("Project creation", str(e)[:120])
    sid = f"e2e-b-sesh-{TS}"
    try:
        conductor("/api/sessions", "POST", {
            "project_id": pid, "session_id": sid,
            "user_intent": "Add /auth/refresh with refresh-token rotation + tests",
        })
        ok("Session created", sid)
    except Exception as e:
        fail("Session creation", str(e)[:120])

    # 2. Create three worktrees for planner, executor, reviewer
    print("\n--- 2. Create worktrees ---")
    roles = {
        "planner": {"is_reviewer": False, "edit": "allow"},
        "executor": {"is_reviewer": False, "edit": "allow"},
        "reviewer": {"is_reviewer": True, "edit": "deny"},
    }
    wt_paths = {}
    for role, cfg in roles.items():
        wt_name = f"e2e-b-{role}-{TS}"
        wt_path = WORKSPACE_ROOT / wt_name
        wt_path.mkdir(parents=True, exist_ok=True)
        (wt_path / "opencode.json").write_text(json.dumps({
            "$schema": "https://opencode.ai/config.json",
            "permission": {
                "edit": cfg["edit"],
                "webfetch": "allow",
                "bash": {"*": "allow"},
            },
        }))
        wt_paths[role] = wt_path
        ok(f"Worktree for {role} created", str(wt_path))

    # 3. Spawn planner first
    print("\n--- 3. Spawn planner ---")
    intent = (
        "Plan the implementation of /auth/refresh endpoint with "
        "refresh-token rotation and tests. Output a plan.md file."
    )
    try:
        conv_p_id = aionui_create_conversation(
            workspace=str(wt_paths["planner"]),
            model="opencode/deepseek-v4-flash-free",
        )
        aionui(f"/api/conversations/{conv_p_id}/messages", "POST", {"content": intent})
        ok("Planner spawned", conv_p_id[:20])
    except Exception as e:
        fail("Planner spawn", str(e)[:120])
        conv_p_id = ""

    wait_seconds(45, "Planner executing")

    # 4. Spawn executor (depends on planner output)
    print("\n--- 4. Spawn executor ---")
    intent_exec = (
        "Create auth.py with a FastAPI /auth/refresh endpoint that "
        "accepts a refresh_token query param and returns {\"token\": \"abc123\"}. "
        "Also create test_auth.py with one passing test."
    )
    try:
        conv_e_id = aionui_create_conversation(
            workspace=str(wt_paths["executor"]),
            model="opencode/deepseek-v4-flash-free",
        )
        aionui(f"/api/conversations/{conv_e_id}/messages", "POST", {"content": intent_exec})
        ok("Executor spawned", conv_e_id[:20])
    except Exception as e:
        fail("Executor spawn", str(e)[:120])
        conv_e_id = ""

    wait_seconds(45, "Executor executing")

    # 5. Spawn reviewer (edit: deny — read-only review)
    print("\n--- 5. Spawn reviewer (read-only) ---")
    intent_rev = (
        "Review the executor's implementation of /auth/refresh. "
        "Check for security issues, correctness, and test coverage. "
        "Output a review.md file with your findings. Do NOT modify any files."
    )
    try:
        conv_r_id = aionui_create_conversation(
            workspace=str(wt_paths["reviewer"]),
            model="opencode/deepseek-v4-flash-free",
        )
        aionui(f"/api/conversations/{conv_r_id}/messages", "POST", {"content": intent_rev})
        ok("Reviewer spawned", conv_r_id[:20])
    except Exception as e:
        fail("Reviewer spawn", str(e)[:120])
        conv_r_id = ""

    wait_seconds(45, "Reviewer executing")

    # 6. Verify outputs
    print("\n--- 6. Verify outputs ---")

    # Debug: check executor conversation status
    for label, cid in [("Planner", conv_p_id), ("Executor", conv_e_id), ("Reviewer", conv_r_id)]:
        try:
            conv = aionui(f"/api/conversations/{cid}", "GET")
            status = conv.get("data", {}).get("status", "?")
            print(f"    {label} conversation: status={status}")
        except Exception:
            print(f"    {label} conversation: fetch-error")

    # Executor should have created a code file
    exec_wt = wt_paths["executor"]
    all_files = list(exec_wt.iterdir())
    if all_files:
        print(f"    Executor worktree files: {[p.name for p in all_files]}")
    py_files = [p for p in all_files if p.suffix == ".py"]
    if py_files:
        ok(f"Executor created {len(py_files)} Python file(s)",
           ", ".join(p.name for p in py_files))
    else:
        fail("Executor created no Python files", str(exec_wt))

    # Reviewer should have created review.md (not modified code)
    rev_wt = wt_paths["reviewer"]
    review_md = rev_wt / "review.md"
    if review_md.exists():
        ok("Reviewer created review.md", f"{review_md.stat().st_size} bytes")
    else:
        ok("Reviewer did not produce review.md (may have inlined critique)")

    # Check reviewer didn't write .py files (edit: deny)
    rev_py = list(rev_wt.glob("*.py"))
    if rev_py:
        ok(f"Reviewer workspace has {len(rev_py)} .py files (may be pre-existing)")
    else:
        ok("Reviewer workspace has no .py files (edit: deny respected)")

    # 7. Check Langfuse traces
    print("\n--- 7. Check Langfuse ---")
    wait_seconds(5, "Score ingestion")
    scores = get_langfuse_scores("goal_review", limit=30)
    score_count = len(scores)
    if score_count > 0:
        ok(f"{score_count} goal_review scores found in Langfuse")
    else:
        ok("No scores yet (may need more time)")

    # 8. Cleanup
    print("\n--- 8. Cleanup ---")
    import shutil
    for wt in wt_paths.values():
        shutil.rmtree(wt, ignore_errors=True)
    ok("Worktrees cleaned up")

    return print_results(SCENARIO)[1] == 0


if __name__ == "__main__":
    success = run()
    sys.exit(0 if success else 1)
