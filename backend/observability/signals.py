"""Derive watcher-readable signals from the merged event stream.

Each function operates on a list of Event dicts (from ``normalize.merge_events``)
and returns a deterministic scalar that the watcher (File 16) can poll.
"""
from __future__ import annotations

import time

from backend.observability.sources.cli_jsonl import (
    token_rate,
    last_activity_ts,
    terminal_marker,
    detect_quota_signal,
)
from backend.observability.sources.worktree_fs import (
    fs_changed_recently,
    git_diff_stat,
)

__all__ = [
    "token_rate",
    "last_activity_ts",
    "terminal_marker",
    "detect_quota_signal",
    "fs_changed_recently",
    "git_diff_stat",
    "compute_session_signals",
    "verdict_from_signals",
]


def compute_session_signals(
    events: list[dict],
    worktree_path: str | None = None,
    pid: int | None = None,
    session_id: str | None = None,
) -> dict:
    """Produce a ``session_signals`` snapshot dict.

    Used by the watcher (File 16) and the ingest pipeline (File 15).
    """
    now = time.time()
    return {
        "session_id": session_id or "",
        "ts": now,
        "token_rate": token_rate(events),
        "last_activity": last_activity_ts(events) or now,
        "terminal": terminal_marker(events),
        "quota_suspected": detect_quota_signal(events),
        "pid_alive": _pid_alive(pid) if pid else False,
        "fs_changed": fs_changed_recently(worktree_path) if worktree_path else False,
    }


VERDICT_RUNNING = "running"
VERDICT_DONE = "done"
VERDICT_STALLED = "stalled"
VERDICT_QUOTA = "quota"
VERDICT_CRASHED = "crashed"


def verdict_from_signals(
    sig: dict,
    stall_threshold_s: int = 120,
) -> str:
    """Deterministic multi-signal verdict for the watcher.

    ANDs multiple weak signals to avoid false positives during legitimate
    long thinking or test runs.

    Args:
        sig: Signal snapshot dict from ``compute_session_signals``.
        stall_threshold_s: Seconds of inactivity before considering stalled.

    Returns:
        One of ``"running" | "done" | "stalled" | "quota" | "crashed"``.
    """
    pid_alive = sig.get("pid_alive", False)
    terminal = sig.get("terminal", False)
    quota = sig.get("quota_suspected", False)
    tr = sig.get("token_rate", 0.0)
    fs = sig.get("fs_changed", False)
    last_act = sig.get("last_activity", 0.0)

    if not pid_alive:
        return VERDICT_CRASHED
    if terminal:
        return VERDICT_DONE
    if quota and tr == 0.0 and not fs:
        return VERDICT_QUOTA
    age = time.time() - last_act
    if age > stall_threshold_s and tr == 0.0 and not fs:
        return VERDICT_STALLED
    return VERDICT_RUNNING


def _pid_alive(pid: int) -> bool:
    import os
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False
