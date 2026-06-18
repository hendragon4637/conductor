"""Merge the three event sources into a unified trace tree + signal stream."""
from __future__ import annotations

import time
from typing import Iterator

from backend.observability.sources.aionui_sqlite import aionui_events
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
)


def merge_events(
    conversation_id: str,
    session_id: str,
    worktree_path: str | None = None,
    aionui_db: str | None = None,
) -> list[dict]:
    """Collect and merge events from all three sources into a single time-sorted list.

    Each event dict has at minimum: ``ts``, ``source``, ``type``, ``role``, ``content``,
    ``tokens``, ``metadata``.  A ``parent_id`` field is added during nesting.
    """
    all_events: list[dict] = []

    # Source 1: AionUi conversation
    for ev in aionui_events(conversation_id, db_path=aionui_db):
        all_events.append(ev)

    # Source 2: OpenCode DB + logs
    for ev in opencode_db_events(session_id):
        all_events.append(ev)
    for ev in tail_log_events(session_id):
        all_events.append(ev)

    # Source 3: Worktree filesystem
    if worktree_path:
        for ev in worktree_events(worktree_path):
            all_events.append(ev)

    # Sort by timestamp
    all_events.sort(key=lambda e: e.get("ts", 0))

    # Build parent_id tree: subagent_spawn events nest under their parent
    _build_event_tree(all_events)
    _deduplicate_events(all_events)

    return all_events


def _build_event_tree(events: list[dict]) -> None:
    """Add ``parent_id`` to events so subagent events nest under their parent."""
    # Map session_id -> last event per source
    last_by_source: dict[str, str | None] = {}
    for ev in events:
        src = ev.get("source", "")
        meta = ev.get("metadata", {})
        mid = meta.get("message_id") or meta.get("session_id")

        # AionUi messages are top-level (no parent needed beyond conversation)
        if src == "aionui":
            ev["parent_id"] = None
            last_by_source["aionui"] = mid

        # OpenCode messages nest under session
        elif src == "opencode_db":
            role = ev.get("role", "")
            if role == "assistant":
                # Assistant messages are responses to user messages — find parent
                user_msg_id = _find_last_user_msg_id(events, ev.get("ts", 0))
                ev["parent_id"] = user_msg_id
            else:
                ev["parent_id"] = last_by_source.get("opencode_db")
            last_by_source["opencode_db"] = mid

        # Log events nest under the opencode session
        elif src == "opencode_log":
            ev["parent_id"] = last_by_source.get("opencode_db")

        # Worktree events are top-level progress signals
        elif src == "worktree_fs":
            ev["parent_id"] = None


def _find_last_user_msg_id(events: list[dict], before_ts: float) -> str | None:
    for ev in reversed(events):
        if ev.get("type") == "user_message" and ev.get("ts", 0) < before_ts:
            meta = ev.get("metadata", {})
            return meta.get("message_id")
    return None


def _deduplicate_events(events: list[dict]) -> None:
    """Remove duplicate events by (ts, type, content) signature."""
    seen: set[tuple] = set()
    i = 0
    while i < len(events):
        sig = (
            events[i].get("ts", 0),
            events[i].get("type", ""),
            events[i].get("content", "")[:100],
        )
        if sig in seen:
            events.pop(i)
        else:
            seen.add(sig)
            i += 1


def compute_signal_snapshot(
    events: list[dict],
    worktree_path: str | None = None,
    pid: int | None = None,
) -> dict:
    """Produce a ``session_signals`` row from merged events."""
    now = time.time()
    return {
        "ts": now,
        "token_rate": token_rate(events),
        "last_activity": last_activity_ts(events),
        "terminal": terminal_marker(events),
        "quota_suspected": detect_quota_signal(events),
        "pid_alive": _pid_alive(pid) if pid else False,
        "fs_changed": fs_changed_recently(worktree_path) if worktree_path else False,
    }


def _pid_alive(pid: int) -> bool:
    """Check if a process is alive without sending signals."""
    try:
        import os as _os
        _os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False
