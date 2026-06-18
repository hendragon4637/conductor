from __future__ import annotations

import os
import sys
import sqlite3
import tempfile
import time

sys.path.insert(0, "/opt/aipc/conductor")

from backend.watcher.signals_query import node_signal
from backend.watcher.verdict import verdict, VERDICT_DONE, VERDICT_FAILED, VERDICT_STALLED


AIONUI_DB = os.environ.get(
    "AIONUI_DB",
    "/home/aipc/.config/AionUi/aionui/aionui-backend.db",
)

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


print("\n=== Gate 22-A: cheap query verdict ===")

try:
    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        conn = sqlite3.connect(tmp.name)
        conn.execute(
            """
            CREATE TABLE messages (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                msg_id TEXT,
                type TEXT NOT NULL,
                content TEXT NOT NULL DEFAULT '{}',
                position TEXT,
                status TEXT,
                hidden INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO messages (id, conversation_id, type, content, position, status, created_at) VALUES (?,?,?,?,?,?,?)",
            (
                "m1",
                "conv-error",
                "tips",
                '{"content":"The upstream Agent failed while handling the request","error":{"code":"UNKNOWN_UPSTREAM_ERROR"},"type":"error"}',
                "left",
                "error",
                1000,
            ),
        )
        conn.commit()
        conn.close()

        error_sig = node_signal(tmp.name, ["conv-error"])
    check("synthetic error query has data", error_sig.get("have_data") is True)
    check("query detects upstream error", error_sig.get("any_error") is True)
    check("query returns error code", "UNKNOWN_UPSTREAM_ERROR" in error_sig.get("error_codes", []))
    v = verdict({"thresholds": {"stall_s": 180}}, {**error_sig, "pid_alive": True, "token_rate": 0.0, "fs_changed": False, "last_activity": time.time()})
    check(f"verdict is failed (got {v})", v == VERDICT_FAILED)
except Exception as e:
    print(f"  FAIL: error case exception {e}")
    FAIL += 1

try:
    healthy_sig = node_signal(AIONUI_DB, ["feaf2861", "bd7c4927"])
    check("healthy node has data", healthy_sig.get("have_data") is True)
    check("healthy node no query error", healthy_sig.get("any_error") is False)
    v = verdict({"thresholds": {"stall_s": 180}}, {**healthy_sig, "pid_alive": True, "token_rate": 0.0, "fs_changed": False, "last_activity": time.time()})
    check(f"healthy node done (got {v})", v == VERDICT_DONE)
except Exception as e:
    print(f"  FAIL: healthy case exception {e}")
    FAIL += 1

try:
    stalled_sig = {
        "have_data": True,
        "any_error": False,
        "error_codes": [],
        "last_activity": time.time() - 600,
        "age_s": 600.0,
        "terminal": False,
        "pid_alive": True,
        "token_rate": 0.0,
        "fs_changed": False,
    }
    v = verdict({"thresholds": {"stall_s": 180}}, stalled_sig)
    check(f"stalled node returns stalled (got {v})", v == VERDICT_STALLED)
except Exception as e:
    print(f"  FAIL: stalled case exception {e}")
    FAIL += 1

print(f"\nGate 22-A results: {PASS} passed, {FAIL} failed")
if FAIL:
    raise SystemExit(1)
