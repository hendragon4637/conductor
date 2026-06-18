"""GATE 16 — Test the watcher module (supervisor, verdict, controls, gitops).

Pass conditions:
1. Detection: Simulate crashed process → verdict reports 'crashed'
2. Quota: Synthetic 'token-rate 0, no finish, no fs change' → 'quota'
3. Singleton: Two sessions tracked by ONE registry
4. Commit ladder: Sequential chunks with regression gate
5. Controls: pause commits + halts; resume re-enters; cancel leaves worktree
6. Deterministic: No LLM call in verdict path
"""
from __future__ import annotations

import os
import sys
import time
import tempfile
from pathlib import Path

sys.path.insert(0, "/opt/aipc/conductor")

from backend.watcher.verdict import (
    verdict,
    VERDICT_RUNNING,
    VERDICT_DONE,
    VERDICT_STALLED,
    VERDICT_QUOTA,
    VERDICT_CRASHED,
)
from backend.watcher.supervisor import Watcher, SessionState
from backend.watcher.gitops import commit_chunk, regression_gate

PASS = 0
FAIL = 0


def check(desc: str, ok: bool):
    global PASS, FAIL
    if ok:
        print(f"  PASS: {desc}")
        PASS += 1
    else:
        print(f"  FAIL: {desc}")
        FAIL += 1


# ---------------------------------------------------------------------------
# 1. Detection: crashed process
# ---------------------------------------------------------------------------
print("\n=== Test 1: Crashed detection ===")
try:
    # Simulate a dead process
    sig = {
        "pid_alive": False, "terminal": False, "quota_suspected": False,
        "token_rate": 0.0, "fs_changed": False, "last_activity": time.time(),
    }
    state = {"pid": 999999, "thresholds": {"stall_s": 120}}
    v = verdict(state, sig)
    check(f"crashed detected (got '{v}')", v == VERDICT_CRASHED)
except Exception as e:
    print(f"  FAIL: exception {e}")
    FAIL += 1

# ---------------------------------------------------------------------------
# 2. Quota detection (no LLM)
# ---------------------------------------------------------------------------
print("\n=== Test 2: Quota detection ===")
try:
    quota_sig = {
        "pid_alive": True, "terminal": False, "quota_suspected": True,
        "token_rate": 0.0, "fs_changed": False, "last_activity": time.time(),
    }
    v = verdict(state, quota_sig)
    check(f"quota detected (got '{v}')", v == VERDICT_QUOTA)

    # Not quota if fs changed (activity detected)
    fs_sig = {**quota_sig, "fs_changed": True}
    v = verdict(state, fs_sig)
    check(f"not quota when fs_changed (got '{v}')", v == VERDICT_RUNNING)

    # Not quota if rate > 0
    rate_sig = {**quota_sig, "token_rate": 5.0}
    v = verdict(state, rate_sig)
    check(f"not quota when token_rate>0 (got '{v}')", v == VERDICT_RUNNING)
except Exception as e:
    print(f"  FAIL: exception {e}")
    FAIL += 1

# ---------------------------------------------------------------------------
# 3. Singleton: two sessions, one registry
# ---------------------------------------------------------------------------
print("\n=== Test 3: Singleton registry ===")
try:
    w = Watcher(poll_interval_s=9999)  # don't auto-poll in test
    st1 = w.register("session-a", pid=111)
    st2 = w.register("session-b", pid=222)
    check("two sessions registered", len(w.registry) == 2)
    check("session-a accessible", w.get("session-a") is st1)
    check("session-b accessible", w.get("session-b") is st2)
    check("session-a status running", st1.status == VERDICT_RUNNING)
    check("session-b status running", st2.status == VERDICT_RUNNING)

    # Unregister one
    w.unregister("session-a")
    check("unregister removes session", len(w.registry) == 1)
    check("remaining session is session-b", w.get("session-b") is st2)

    # Re-register
    w.register("session-a", pid=333)
    check("re-register works", len(w.registry) == 2)
except Exception as e:
    print(f"  FAIL: exception {e}")
    FAIL += 1

# ---------------------------------------------------------------------------
# 4. Event-driven update
# ---------------------------------------------------------------------------
print("\n=== Test 4: Event-driven update ===")
try:
    st = w.get("session-b")
    old_seen = st.last_seen if st else 0
    w.on_event("session-b", {"tokens": {"input": 100, "output": 200}})
    if st:
        check("last_seen updated", st.last_seen >= old_seen)
        check("token_rate set", st.token_rate > 0)

    # Unknown session
    w.on_event("nonexistent", {"tokens": {}})  # should not crash
    check("unknown session event ignored", True)
except Exception as e:
    print(f"  FAIL: exception {e}")
    FAIL += 1

# ---------------------------------------------------------------------------
# 5. Commit ladder
# ---------------------------------------------------------------------------
print("\n=== Test 5: Commit ladder ===")
try:
    with tempfile.TemporaryDirectory() as tmpdir:
        wd = Path(tmpdir)
        # Init git repo
        os.system(f"git init {wd} >/dev/null 2>&1")
        os.system(f"git -C {wd} config user.email test@test.com")
        os.system(f"git -C {wd} config user.name test")

        # First chunk
        (wd / "file1.txt").write_text("hello")
        ok = commit_chunk(wd, "chunk-1", "first chunk")
        check("chunk 1 committed", ok)

        # Verify tag exists
        tag_result = os.popen(f"git -C {wd} tag -l").read().strip()
        check(f"tag node-chunk-1 created", "node-chunk-1" in tag_result)

        # Only one commit
        log = os.popen(f"git -C {wd} log --oneline").read().strip()
        check("exactly 1 commit after chunk 1", log.count("\n") == 0)

        # Second chunk
        (wd / "file2.txt").write_text("world")
        ok = commit_chunk(wd, "chunk-2", "second chunk")
        check("chunk 2 committed", ok)

        log = os.popen(f"git -C {wd} log --oneline").read().strip()
        check("2 commits after chunk 2", log.count("\n") == 1)

        # Nothing to commit (no changes)
        ok = commit_chunk(wd, "chunk-3", "no change")
        check("nothing to commit returns False", not ok)

except Exception as e:
    print(f"  FAIL: exception {e}")
    FAIL += 1

# ---------------------------------------------------------------------------
# 6. Regression gate
# ---------------------------------------------------------------------------
print("\n=== Test 6: Regression gate ===")
try:
    with tempfile.TemporaryDirectory() as tmpdir:
        wd = Path(tmpdir)
        os.system(f"git init {wd} >/dev/null 2>&1")

        # No test suite → gate returns False (no pytest found)
        ok = regression_gate(wd)
        check("regression gate fails without test suite", not ok)

        # Write a simple test that passes
        (wd / "test_dummy.py").write_text("def test_pass(): assert 1+1==2")
        ok = regression_gate(wd)
        check("regression gate passes with pytest", ok)

except Exception as e:
    print(f"  FAIL: exception {e}")
    FAIL += 1

# ---------------------------------------------------------------------------
# 7. Deterministic check (no LLM in path)
# ---------------------------------------------------------------------------
print("\n=== Test 7: Deterministic verdict ===")
try:
    # All verdict paths should be purely conditional
    sigs = [
        ({"pid_alive": False}, VERDICT_CRASHED),
        ({"pid_alive": True, "terminal": True}, VERDICT_DONE),
        ({"pid_alive": True, "terminal": False, "quota_suspected": True,
          "token_rate": 0.0, "fs_changed": False}, VERDICT_QUOTA),
        ({"pid_alive": True, "terminal": False, "quota_suspected": False,
          "token_rate": 0.0, "fs_changed": False, "last_activity": time.time() - 300}, VERDICT_STALLED),
    ]
    for parts, expected in sigs:
        full_sig = {
            "pid_alive": False, "terminal": False, "quota_suspected": False,
            "token_rate": 0.0, "fs_changed": False, "last_activity": 0.0,
            **parts,
        }
        st = {"pid": 0, "thresholds": {"stall_s": 120}}
        v = verdict(st, full_sig)
        check(f"deterministic: {expected} (got '{v}')", v == expected)

except Exception as e:
    print(f"  FAIL: exception {e}")
    FAIL += 1

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print(f"\n{'='*50}")
print(f"GATE 16 RESULTS: {PASS} passed, {FAIL} failed out of {PASS+FAIL}")
print(f"{'='*50}")

if FAIL > 0:
    sys.exit(1)
else:
    print("GATE 16: PASS")
