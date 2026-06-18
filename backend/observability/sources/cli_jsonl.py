"""Source adapter: OpenCode DB + structured logs → normalized events.

OpenCode stores session data in SQLite, not JSONL. This adapter:
  1. Queries the OpenCode DB for session/message/token data
  2. Tails the structured log directory for live events
  3. Normalizes both into Event dicts with token usage, model, agent info
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Iterator

OPCODE_DB_PATH = os.path.expanduser("~/.local/share/opencode/opencode.db")
OPCODE_LOG_DIR = os.path.expanduser("~/.local/share/opencode/log")


# ---------------------------------------------------------------------------
# OpenCode DB reader
# ---------------------------------------------------------------------------

def opencode_db_events(session_id: str) -> Iterator[dict]:
    """Yield events from the OpenCode SQLite DB for a given session.

    Reads the ``message`` and ``part`` tables to produce events with
    token counts, model info, and role.
    """
    db = Path(OPCODE_DB_PATH)
    if not db.exists():
        return

    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    try:
        # Session metadata
        row = conn.execute(
            "SELECT id, project_id, title, agent, model, time_created, time_updated "
            "FROM session WHERE id = ?",
            (session_id,),
        ).fetchone()

        if row:
            yield {
                "ts": row["time_created"] / 1000.0,
                "source": "opencode_db",
                "type": "session_start",
                "role": None,
                "content": "",
                "tokens": {},
                "metadata": {
                    "session_id": session_id,
                    "project_id": row["project_id"],
                    "title": row["title"],
                    "agent": row["agent"],
                    "model": row["model"],
                },
            }

        # Messages with token data
        rows = conn.execute(
            "SELECT id, time_created, data FROM message WHERE session_id = ? ORDER BY time_created",
            (session_id,),
        ).fetchall()

        for r in rows:
            data = _parse_json_field(r["data"])
            if not isinstance(data, dict):
                continue

            msg_id = r["id"]
            created = r["time_created"] / 1000.0
            role = data.get("role", "unknown")
            tokens = data.get("tokens", {})
            model = data.get("modelID", data.get("model", ""))
            provider = data.get("providerID", "")
            agent = data.get("agent", "")
            cost = data.get("cost", 0)

            yield {
                "ts": created,
                "source": "opencode_db",
                "type": f"{role}_message",
                "role": role,
                "content": _extract_content(conn, msg_id),
                "tokens": {
                    "input": tokens.get("input", 0),
                    "output": tokens.get("output", 0),
                    "reasoning": tokens.get("reasoning", 0),
                    "cache_read": tokens.get("cache", {}).get("read", 0),
                    "cache_write": tokens.get("cache", {}).get("write", 0),
                },
                "metadata": {
                    "session_id": session_id,
                    "message_id": msg_id,
                    "model": model,
                    "provider": provider,
                    "agent": agent,
                    "cost": cost,
                },
            }

    finally:
        conn.close()


def _parse_json_field(value: str | bytes) -> object:
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value


def _extract_content(conn: sqlite3.Connection, message_id: str) -> str:
    """Concatenate part text for a message."""
    rows = conn.execute(
        "SELECT data FROM part WHERE message_id = ? ORDER BY rowid",
        (message_id,),
    ).fetchall()
    texts = []
    for r in rows:
        data = _parse_json_field(r["data"])
        if isinstance(data, dict) and data.get("type") == "text":
            texts.append(data.get("text", ""))
    return "\n".join(texts)


# ---------------------------------------------------------------------------
# Structured log tail parser
# ---------------------------------------------------------------------------

_LOG_LINE_RE = re.compile(
    r"(?P<level>\w+)\s+(?P<timestamp>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})"
    r"(?:\s+\+\d+ms)?\s+(?P<rest>.*)"
)


def tail_log_events(session_id: str, max_lines: int = 5000) -> Iterator[dict]:
    """Parse the most recent OpenCode structured log for session-related events.

    The logs are structured text with ``key=value`` pairs.  This function
    searches for lines mentioning the given ``session_id``.
    """
    log_dir = Path(OPCODE_LOG_DIR)
    if not log_dir.exists():
        return

    log_files = sorted(log_dir.glob("*.log"), reverse=True)
    if not log_files:
        return

    lines_read = 0
    for lf in log_files[:3]:  # check 3 most recent logs
        if lines_read >= max_lines:
            break
        try:
            text = lf.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for line in text.splitlines():
            if lines_read >= max_lines:
                break
            lines_read += 1
            if session_id not in line:
                continue

            m = _LOG_LINE_RE.match(line)
            if not m:
                continue

            ts_str = m.group("timestamp")
            try:
                ts = time.mktime(time.strptime(ts_str, "%Y-%m-%dT%H:%M:%S"))
            except (ValueError, TypeError):
                ts = time.time()

            rest = m.group("rest")
            pairs = _parse_kv_pairs(rest)

            yield {
                "ts": ts,
                "source": "opencode_log",
                "type": pairs.get("service", "unknown"),
                "role": None,
                "content": rest,
                "tokens": {},
                "metadata": {
                    "session_id": session_id,
                    "log_level": m.group("level"),
                    **pairs,
                },
            }


def _parse_kv_pairs(text: str) -> dict[str, str]:
    """Parse ``key=value`` pairs from the structured log text."""
    pairs: dict[str, str] = {}
    # Split on whitespace, look for k=v
    for part in text.split():
        if "=" in part and not part.startswith("="):
            k, _, v = part.partition("=")
            pairs[k.strip()] = v.strip()
    return pairs


# ---------------------------------------------------------------------------
# Derived signals
# ---------------------------------------------------------------------------

def token_rate(events: list[dict], window_s: int = 120) -> float:
    """Total tokens (input+output) per second over the last *window_s* seconds."""
    now = time.time()
    cutoff = now - window_s
    recent = [
        e
        for e in events
        if e.get("ts", 0) >= cutoff and "tokens" in e
    ]
    if not recent:
        return 0.0
    total = sum(
        e["tokens"].get("input", 0) + e["tokens"].get("output", 0)
        for e in recent
        if isinstance(e.get("tokens"), dict)
    )
    elapsed = min(now - cutoff, window_s)
    return total / elapsed if elapsed > 0 else 0.0


def last_activity_ts(events: list[dict]) -> float:
    """Latest timestamp across all events."""
    ts_list = [e.get("ts", 0) for e in events if isinstance(e.get("ts"), (int, float))]
    return max(ts_list) if ts_list else 0.0


def terminal_marker(events: list[dict]) -> bool:
    """True if an explicit finish/stop/complete event is found."""
    terminal_types = {"finish", "stop", "complete", "session_end", "session_complete"}
    return any(e.get("type") in terminal_types for e in events)


def detect_quota_signal(events: list[dict]) -> bool:
    """Infer silent quota death: token_rate == 0, no terminal marker, recent events exist.

    This catches the case where the free-tier OpenCode hits a rate/usage limit
    without emitting an explicit error line.
    """
    late_events = [e for e in events if e.get("ts", 0) > time.time() - 300]
    if not late_events:
        return False  # no recent activity -> not a quota inference
    rate = token_rate(late_events, window_s=120)
    terminal = terminal_marker(late_events)
    return rate == 0.0 and not terminal
