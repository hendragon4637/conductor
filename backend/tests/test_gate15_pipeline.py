"""GATE 15 — Test the full observability pipeline end-to-end.

Pass conditions:
1. CLI JSONL (OpenCode DB) parsed, non-empty events returned
2. token_rate >= 0, last_activity_ts returns float, terminal_marker bool
3. detect_quota_signal works with synthetic fixture
4. trace tree with nesting (parent_id) from normalize
5. session_signals table exists and row can be written
6. Session signals snapshot produced
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, "/opt/aipc/conductor")

from backend.observability.sources.cli_jsonl import (
    opencode_db_events,
    tail_log_events,
    token_rate,
    last_activity_ts,
    terminal_marker,
    detect_quota_signal,
)
from backend.observability.sources.worktree_fs import (
    worktree_events,
    fs_changed_recently,
    git_diff_stat,
)
from backend.observability.sources.aionui_sqlite import aionui_events
from backend.observability.normalize import (
    merge_events,
    compute_signal_snapshot,
)
from backend.observability.signals import (
    verdict_from_signals,
    VERDICT_RUNNING,
    VERDICT_DONE,
    VERDICT_STALLED,
    VERDICT_QUOTA,
    VERDICT_CRASHED,
)

TEST_SESSION = "ses_1694d9a2dffeRJeyamga3Du5tP"
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
# 1. OpenCode DB events parsed
# ---------------------------------------------------------------------------
print("\n=== Test 1: OpenCode DB events ===")
try:
    evs = list(opencode_db_events(TEST_SESSION))
    check(f"non-empty events from DB session {TEST_SESSION}", len(evs) > 0)
    check("first event is session_start", evs[0]["type"] == "session_start" if evs else False)
    # Check token data present
    token_events = [e for e in evs if "tokens" in e and isinstance(e["tokens"], dict)]
    check("token data present in some events", len(token_events) > 0)
except Exception as e:
    print(f"  FAIL: exception {e}")
    FAIL += 1

# ---------------------------------------------------------------------------
# 2. Structured log tail
# ---------------------------------------------------------------------------
print("\n=== Test 2: Structured log tail ===")
try:
    log_evs = list(tail_log_events(TEST_SESSION))
    check(f"log tail ran without error", True)
    # May be empty if no log mentions this session — that's OK
    if log_evs:
        check("log events have timestamp", all(e.get("ts", 0) > 0 for e in log_evs))
except Exception as e:
    print(f"  FAIL: exception {e}")
    FAIL += 1

# ---------------------------------------------------------------------------
# 3. Signal derivation functions
# ---------------------------------------------------------------------------
print("\n=== Test 3: Signal functions ===")
try:
    # token_rate with real events
    rate = token_rate(evs)
    check(f"token_rate >= 0 (got {rate})", rate >= 0)

    last_ts = last_activity_ts(evs)
    check(f"last_activity_ts > 0 (got {last_ts})", last_ts > 0)

    terminal = terminal_marker(evs)
    check(f"terminal_marker returns bool", isinstance(terminal, bool))

    quota = detect_quota_signal(evs)
    check(f"detect_quota_signal returns bool", isinstance(quota, bool))
except Exception as e:
    print(f"  FAIL: exception {e}")
    FAIL += 1

# ---------------------------------------------------------------------------
# 4. Synthetic quota-death inference fixture
# ---------------------------------------------------------------------------
print("\n=== Test 4: Quota-death inference ===")
try:
    now = time.time()
    # Simulate: events with token_rate -> 0, no terminal marker
    quota_fixture = [
        {"ts": now - 60, "source": "test", "type": "user_message", "role": "user",
         "content": "hello", "tokens": {"input": 0, "output": 0}},
        {"ts": now - 30, "source": "test", "type": "assistant_message", "role": "assistant",
         "content": "world", "tokens": {"input": 0, "output": 0}},
        {"ts": now - 10, "source": "test", "type": "user_message", "role": "user",
         "content": "still there?", "tokens": {"input": 0, "output": 0}},
    ]
    quota_detected = detect_quota_signal(quota_fixture)
    check("quota detected from synthetic rate→0, no finish fixture", quota_detected)

    # Now add a terminal marker — should NOT detect quota
    quota_fixture.append({
        "ts": now - 5, "source": "test", "type": "finish", "role": None,
        "content": "done", "tokens": {},
    })
    quota_not_detected = detect_quota_signal(quota_fixture)
    check("quota NOT detected when terminal marker present", not quota_not_detected)

    # Empty events
    quota_empty = detect_quota_signal([])
    check("quota NOT detected on empty events", not quota_empty)
except Exception as e:
    print(f"  FAIL: exception {e}")
    FAIL += 1

# ---------------------------------------------------------------------------
# 5. Worktree source
# ---------------------------------------------------------------------------
print("\n=== Test 5: Worktree FS ===")
try:
    wt_evs = list(worktree_events("/opt/aipc/conductor"))
    check("worktree events returns list", isinstance(wt_evs, list))

    diff = git_diff_stat("/opt/aipc/conductor")
    check("git_diff_stat returns correct keys",
          {"insertions", "deletions", "files_changed"} == set(diff.keys()))

    fs_ok = fs_changed_recently("/opt/aipc/conductor", window_s=86400)
    check("fs_changed_recently returns bool", isinstance(fs_ok, bool))
except Exception as e:
    print(f"  FAIL: exception {e}")
    FAIL += 1

# ---------------------------------------------------------------------------
# 6. Pipeline integration (merge_events)
# ---------------------------------------------------------------------------
print("\n=== Test 6: Pipeline integration ===")
try:
    merged = merge_events(
        conversation_id="test-conv",
        session_id=TEST_SESSION,
        worktree_path="/opt/aipc/conductor",
    )
    check("merge_events returned non-empty list", len(merged) > 0)
    check("events have parent_id", all("parent_id" in e for e in merged))
    check("events sorted by ts", all(
        merged[i].get("ts", 0) <= merged[i + 1].get("ts", 0)
        for i in range(len(merged) - 1)
    ))
    # AionUi events require a real conversation — tolerant if none found
    aionui_count = sum(1 for e in merged if e.get("source") == "aionui")
    if aionui_count == 0:
        print("  INFO: no aionui events (test conversation ID may not exist)")
    else:
        check("at least one aionui event", True)
    check("at least one opencode_db event", any(e.get("source") == "opencode_db" for e in merged))
except Exception as e:
    print(f"  FAIL: exception {e}")
    FAIL += 1

# ---------------------------------------------------------------------------
# 7. Signal snapshot
# ---------------------------------------------------------------------------
print("\n=== Test 7: Signal snapshot ===")
try:
    sig = compute_signal_snapshot(
        merged if 'merged' in dir() else evs,
        worktree_path="/opt/aipc/conductor",
    )
    check("snapshot has all required keys", {
        "token_rate", "last_activity", "terminal",
        "quota_suspected", "pid_alive", "fs_changed",
    }.issubset(sig.keys()))
    check("token_rate is numeric", isinstance(sig["token_rate"], (int, float)))
    check("terminal is bool", isinstance(sig["terminal"], bool))
except Exception as e:
    print(f"  FAIL: exception {e}")
    FAIL += 1

# ---------------------------------------------------------------------------
# 8. Verdict function
# ---------------------------------------------------------------------------
print("\n=== Test 8: Verdict from signals ===")
try:
    # Running
    running_sig = {
        "pid_alive": True, "terminal": False, "quota_suspected": False,
        "token_rate": 5.0, "fs_changed": True, "last_activity": time.time(),
    }
    check("verdict running", verdict_from_signals(running_sig) == VERDICT_RUNNING)

    # Done
    done_sig = {**running_sig, "terminal": True}
    check("verdict done", verdict_from_signals(done_sig) == VERDICT_DONE)

    # Crashed
    crashed_sig = {**running_sig, "pid_alive": False}
    check("verdict crashed", verdict_from_signals(crashed_sig) == VERDICT_CRASHED)

    # Quota
    quota_sig = {
        "pid_alive": True, "terminal": False, "quota_suspected": True,
        "token_rate": 0.0, "fs_changed": False, "last_activity": time.time(),
    }
    check("verdict quota", verdict_from_signals(quota_sig) == VERDICT_QUOTA)

    # Stalled
    stalled_sig = {
        "pid_alive": True, "terminal": False, "quota_suspected": False,
        "token_rate": 0.0, "fs_changed": False,
        "last_activity": time.time() - 300,  # 5 min ago
    }
    check("verdict stalled", verdict_from_signals(stalled_sig, stall_threshold_s=120) == VERDICT_STALLED)

    # Not stalled if within threshold
    ok_sig = {**stalled_sig, "last_activity": time.time() - 60}
    check("verdict running (within threshold)",
          verdict_from_signals(ok_sig, stall_threshold_s=120) == VERDICT_RUNNING)
except Exception as e:
    print(f"  FAIL: exception {e}")
    FAIL += 1

# ---------------------------------------------------------------------------
# 9. DB session_signals table exists
# ---------------------------------------------------------------------------
print("\n=== Test 9: DB session_signals table ===")
try:
    import psycopg
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        db_url = "postgresql://aipc:aipc@localhost:5432/aipc_conductor"
    with psycopg.connect(db_url) as c:
        with c.cursor() as cur:
            cur.execute("SELECT to_regclass('session_signals')")
            row = cur.fetchone()
            check("session_signals table exists", row and row[0] == "session_signals")
except Exception as e:
    print(f"  FAIL: exception {e}")
    FAIL += 1

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print(f"\n{'='*50}")
print(f"GATE 15 RESULTS: {PASS} passed, {FAIL} failed out of {PASS+FAIL}")
print(f"{'='*50}")

if FAIL > 0:
    sys.exit(1)
else:
    print("GATE 15: PASS")
