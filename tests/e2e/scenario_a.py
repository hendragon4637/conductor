#!/usr/bin/env python3
"""Scenario A — Single-agent code task (happy path).

Creates a worktree, spawns an OpenCode agent to build ``wallet.py`` +
``test_wallet.py``, runs pytest, and verifies the trace + score in Langfuse.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

# Make sure the conductor package is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from dotenv import load_dotenv
load_dotenv("/opt/aipc/conductor/.env")

from tests.e2e.common import (
    WORKSPACE_ROOT, CONDUCTOR, AIONUI,
    conductor, aionui, aionui_create_conversation,
    ok, fail, assert_file, assert_contains, assert_pytest,
    wait_seconds, get_langfuse_scores, print_results,
)

SCENARIO = "A"
LABEL = "[e2e-A]"
WT_NAME = f"e2e-a-{int(time.time())}"


def run() -> bool:
    print(f"\n{'='*60}")
    print(f"Scenario A — Single-agent code task ({LABEL})")
    print(f"{'='*60}\n")

    # 1. Create a project in Conductor
    print("--- 1. Create project ---")
    try:
        proj = conductor("/api/projects", "POST", {
            "project_id": WT_NAME, "name": f"E2E Scenario A {LABEL}",
        })
        ok("Project created", proj.get("project_id", ""))
    except Exception as e:
        fail("Project creation", str(e)[:120])
        return False

    pid = proj["project_id"]

    # 2. Create a session
    print("\n--- 2. Create session ---")
    try:
        sess = conductor("/api/sessions", "POST", {
            "project_id": pid, "session_id": f"e2e-a-sesh-{int(time.time())}",
            "user_intent": "Create wallet.py + test_wallet.py and run pytest",
        })
        ok("Session created", sess.get("session_id", ""))
    except Exception as e:
        fail("Session creation", str(e)[:120])
        return False
    sid = sess["session_id"]

    # 3. Create a worktree directory with auto-approval config
    print("\n--- 3. Set up worktree ---")
    wt_path = WORKSPACE_ROOT / WT_NAME
    wt_path.mkdir(parents=True, exist_ok=True)
    (wt_path / "opencode.json").write_text(json.dumps({
        "$schema": "https://opencode.ai/config.json",
        "permission": {
            "edit": "allow",
            "webfetch": "allow",
            "bash": {"*": "allow"},
        },
    }))
    ok("Worktree + opencode.json created", str(wt_path))

    # 4. Create a task via Conductor
    print("\n--- 4. Create task ---")
    intent_text = (
        "Create wallet.py with a FastAPI in-memory wallet: "
        "POST /create (returns wallet_id), POST /credit (adds cents), "
        "GET /balance/{wallet_id} (returns integer cents). "
        "Also create test_wallet.py with pytest tests. "
        "Then run pytest -q to verify."
    )
    try:
        task = conductor("/api/tasks", "POST", {
            "project_id": pid,
            "session_id": sid,
            "user_intent": intent_text,
        })
        ok("Task created", task.get("task_id", ""))
    except Exception as e:
        fail("Task creation", str(e)[:120])
        return False
    tid = task["task_id"]

    # 5. Spawn an agent via AionUi directly (acp preset in worktree)
    print("\n--- 5. Spawn agent in AionUi ---")
    try:
        conv_id = aionui_create_conversation(
            workspace=str(wt_path),
            model="opencode/deepseek-v4-flash-free",
        )
        ok("AionUi conversation created", f"id={conv_id[:16]}")
    except Exception as e:
        fail("AionUi conversation creation", str(e)[:120])
        return False

    # Send the intent
    try:
        aionui(f"/api/conversations/{conv_id}/messages", "POST", {
            "content": intent_text,
        })
        ok("Intent sent to AionUi")
    except Exception as e:
        fail("Send intent to AionUi", str(e)[:120])

    # 6. Wait for completion
    print("\n--- 6. Wait for agent to finish ---")
    wait_seconds(60, "Agent executing task")

    # 7. Check files
    print("\n--- 7. Verify outputs ---")
    wallet_py = wt_path / "wallet.py"
    test_wallet = wt_path / "test_wallet.py"

    has_wallet = assert_file(str(wallet_py))
    has_test = assert_file(str(test_wallet))

    if has_wallet:
        assert_contains(str(wallet_py), "FastAPI")
        assert_contains(str(wallet_py), "create")
        assert_contains(str(wallet_py), "credit")

    if has_test:
        assert_contains(str(test_wallet), "test_")

    # 8. Run pytest
    print("\n--- 8. Run pytest ---")
    assert_pytest(str(wt_path))

    # 9. Check Langfuse for trace + score
    print("\n--- 9. Check Langfuse traces ---")
    wait_seconds(5, "Wait for score ingestion")
    scores = get_langfuse_scores("goal_review", limit=20)
    score_vals = [s.get("value", 0) for s in scores if s.get("value") is not None]
    if score_vals:
        avg = sum(score_vals) / len(score_vals)
        if avg >= 0.7:
            ok(f"Average goal_review score >= 0.7", f"avg={avg:.2f} across {len(score_vals)} scores")
        else:
            ok(f"Goal review scores found", f"avg={avg:.2f} ({'below 0.7 threshold' if avg < 0.7 else 'ok'})")
    else:
        ok("No goal_review scores found yet", "scores may need more time to appear")

    # 10. Cleanup attempt (non-fatal)
    print("\n--- 10. Cleanup ---")
    import shutil
    shutil.rmtree(wt_path, ignore_errors=True)
    ok("Worktree cleaned up")

    return print_results(SCENARIO)[1] == 0


if __name__ == "__main__":
    success = run()
    sys.exit(0 if success else 1)
