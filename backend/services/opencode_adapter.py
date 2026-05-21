"""
OpenCode native data adapter — SQLite-backed.

Reads ~/.local/share/opencode/opencode.db and ingests sessions referenced by
active traces into the Conductor DB.

Idempotent: uses observations.source_fingerprint as upsert key.

Run via cron every 5 min (or on-demand from the API).

NOTE: This adapter reads from OpenCode's SQLite database, not from directory-based
session/message/part files. OpenCode v1.14+ stores all trajectory data in
opencode.db (see 01_prerequisites_findings.md for schema details).
"""
from __future__ import annotations
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from uuid import UUID

from dotenv import load_dotenv
import psycopg
from psycopg.rows import dict_row

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

OPENCODE_DB_PATH = Path(os.environ.get("OPENCODE_DB_PATH", ""))
DB_URL = os.environ["DATABASE_URL"]

# Receipt marker the system_prompt instructs the model to emit
RECEIPT_MARKER = "__CONTRIBUTION_RECEIPT__:"

# Part types we skip (internal bookkeeping)
SKIP_TYPES = frozenset({"step-start", "step-finish", "compaction"})

# Tool types we map to tool_call observations
TOOL_TYPES = frozenset({"tool"})


# ──────────────────────── connections ────────────────────────

def pg_conn():
    return psycopg.connect(DB_URL, row_factory=dict_row)


def oc_conn() -> sqlite3.Connection:
    """Open a read-only connection to OpenCode's SQLite DB."""
    if not OPENCODE_DB_PATH.is_file():
        raise FileNotFoundError(f"OpenCode DB not found: {OPENCODE_DB_PATH}")
    c = sqlite3.connect(str(OPENCODE_DB_PATH.absolute()))
    c.row_factory = sqlite3.Row
    return c


# ──────────────────────── helpers ────────────────────────

def _ts_to_iso(ts) -> Optional[datetime]:
    """OpenCode uses ms epoch ints. Be defensive."""
    if ts is None:
        return None
    try:
        if isinstance(ts, (int, float)) and ts > 1e12:
            return datetime.fromtimestamp(ts / 1000.0, tz=timezone.utc)
        if isinstance(ts, (int, float)):
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        if isinstance(ts, str):
            return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None
    return None


def _safe_json(s: Any) -> Optional[str]:
    """JSON-dumps if not None, else returns None."""
    if s is None:
        return None
    return json.dumps(s)


# ──────────────────────── query OpenCode SQLite ────────────────────────

def query_session(session_id: str) -> Optional[dict]:
    """Get session metadata from OpenCode SQLite."""
    conn = oc_conn()
    try:
        row = conn.execute(
            "SELECT id, slug, agent, model, time_created, time_updated FROM session WHERE id = ?",
            (session_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def query_messages_and_parts(session_id: str) -> list[dict]:
    """
    Return all messages with their parts for a session, ordered by time_created.
    Each entry: {msg_id, msg_data, role, agent, part_id, part_data, part_type,
                 part_created, part_updated}
    """
    conn = oc_conn()
    try:
        rows = conn.execute(
            """
            SELECT
              m.id AS msg_id,
              m.data AS msg_data,
              m.time_created AS msg_created,
              p.id AS part_id,
              p.data AS part_data,
              p.time_created AS part_created,
              p.time_updated AS part_updated
            FROM message m
            JOIN part p ON p.message_id = m.id
            WHERE m.session_id = ?
            ORDER BY m.time_created ASC, p.time_created ASC
            """,
            (session_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ──────────────────────── mapping ────────────────────────

def parse_part_data(raw: Any) -> dict:
    """Parse part.data JSON. Returns dict (empty if invalid)."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


def parse_msg_data(raw: Any) -> dict:
    """Parse message.data JSON."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


def part_to_observation(
    *,
    trace_id: UUID,
    session_id: str,
    msg_id: str,
    msg_data: dict,
    part_id: str,
    part_data: dict,
    part_created: Optional[int],
    part_updated: Optional[int],
    step_index: int,
) -> Optional[dict[str, Any]]:
    """Convert one OpenCode part + message row to a Conductor observation dict."""
    ptype = part_data.get("type", "text")

    # Skip internal bookkeeping parts
    if ptype in SKIP_TYPES:
        return None

    fingerprint = f"opencode:{session_id}:{msg_id}:{part_id}"

    # Common base
    obs = dict(
        source_fingerprint=fingerprint,
        trace_id=str(trace_id),
        step_index=step_index,
        tokens_input=None,
        tokens_output=None,
        status="ok",
        error=None,
        started_at=_ts_to_iso(part_created or None),
        ended_at=_ts_to_iso(part_updated or part_created or None),
    )

    role = msg_data.get("role", "assistant")

    if ptype in TOOL_TYPES:
        # Tool call (bash, read, write, lsp)
        obs["type"] = "tool_call"
        obs["tool_name"] = part_data.get("tool")
        state = part_data.get("state") or {}
        obs["input"] = _safe_json(state.get("input"))
        obs["output"] = _safe_json(state.get("output"))
        obs["reasoning_text"] = part_data.get("text")

        # Determine status from state
        st = state.get("status", "completed")
        if st == "error":
            obs["status"] = "error"
            obs["error"] = state.get("error")
        elif st == "running":
            obs["status"] = "running"

        # Token info from state if available
        usage = state.get("usage") or {}
        obs["tokens_input"] = usage.get("inputTokens") or msg_data.get("tokens", {}).get("input")
        obs["tokens_output"] = usage.get("outputTokens") or msg_data.get("tokens", {}).get("output")

    elif ptype == "reasoning":
        obs["type"] = "llm_call"
        obs["tool_name"] = None
        obs["input"] = None
        obs["output"] = None
        obs["reasoning_text"] = (part_data.get("text") or "")[:5000]

    elif ptype == "text":
        obs["type"] = "message"
        obs["tool_name"] = None
        obs["input"] = _safe_json({"role": role, "text": (part_data.get("text") or "")[:2000]})
        obs["output"] = None
        obs["reasoning_text"] = None

    elif ptype == "patch":
        obs["type"] = "file_edit"
        obs["tool_name"] = part_data.get("tool")
        obs["input"] = _safe_json(part_data.get("state", {}).get("input"))
        obs["output"] = _safe_json(part_data.get("state", {}).get("output"))
        obs["reasoning_text"] = None

    else:
        # Unknown type — still record as-is
        obs["type"] = ptype
        obs["tool_name"] = part_data.get("tool")
        obs["input"] = _safe_json(part_data.get("input"))
        obs["output"] = _safe_json(part_data.get("output"))
        obs["reasoning_text"] = part_data.get("text")

    return obs


# ──────────────────────── detect contribution receipt ────────────────────────

def extract_receipt(text: str) -> Optional[dict]:
    """
    Look for a line: `__CONTRIBUTION_RECEIPT__:{...json...}`
    Returns the parsed dict or None.
    """
    if not text or RECEIPT_MARKER not in text:
        return None
    idx = text.find(RECEIPT_MARKER) + len(RECEIPT_MARKER)
    candidate = text[idx:].strip()
    if not candidate.startswith("{"):
        return None
    depth = 0
    end = -1
    in_string = False
    escape = False
    for i, ch in enumerate(candidate):
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"' and not escape:
            in_string = not in_string
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end == -1:
        return None
    try:
        return json.loads(candidate[:end])
    except json.JSONDecodeError:
        return None


# ──────────────────────── upsert observations ────────────────────────

def upsert_observations(observations: list[dict]) -> int:
    """Bulk upsert by source_fingerprint. Returns count of new+updated rows."""
    if not observations:
        return 0

    sql = """
    INSERT INTO observations (
      source_fingerprint, trace_id, step_index, type, tool_name,
      input, output, reasoning_text, tokens_input, tokens_output,
      status, error, started_at, ended_at
    ) VALUES (
      %(source_fingerprint)s, %(trace_id)s, %(step_index)s, %(type)s, %(tool_name)s,
      %(input)s::jsonb, %(output)s::jsonb, %(reasoning_text)s,
      %(tokens_input)s, %(tokens_output)s,
      %(status)s, %(error)s, %(started_at)s, %(ended_at)s
    )
    ON CONFLICT (source_fingerprint) DO UPDATE SET
      output = COALESCE(EXCLUDED.output, observations.output),
      ended_at = COALESCE(EXCLUDED.ended_at, observations.ended_at),
      status = COALESCE(EXCLUDED.status, observations.status),
      error = COALESCE(EXCLUDED.error, observations.error);
    """
    with pg_conn() as c, c.cursor() as cur:
        cur.executemany(sql, observations)
        return cur.rowcount


def update_trace_observation_count(trace_id: UUID) -> None:
    with pg_conn() as c, c.cursor() as cur:
        cur.execute(
            """
            UPDATE traces
               SET total_observations = (SELECT COUNT(*) FROM observations WHERE trace_id = %s)
             WHERE trace_id = %s
            """,
            (str(trace_id), str(trace_id)),
        )


# ──────────────────────── per-trace ingest ────────────────────────

def ingest_trace(trace: dict) -> dict:
    """
    Ingest all new observations for one trace.
    Detect completion. Return summary dict.
    """
    summary = {
        "trace_id": trace["trace_id"],
        "cli_session_id": trace["cli_session_id"],
        "new_observations": 0,
        "session_found": False,
        "receipt_found": False,
        "completion_triggered": False,
    }

    sid = trace["cli_session_id"]
    if not sid:
        return summary

    # Verify session exists in OpenCode DB
    try:
        session = query_session(sid)
        if session is None:
            return summary
        summary["session_found"] = True
    except (FileNotFoundError, sqlite3.Error):
        return summary

    # Get messages and parts for this session
    try:
        rows = query_messages_and_parts(sid)
    except (FileNotFoundError, sqlite3.Error):
        return summary

    if not rows:
        return summary

    observations = []
    receipt = None

    for idx, row in enumerate(rows):
        msg_data = parse_msg_data(row.get("msg_data"))
        part_data = parse_part_data(row.get("part_data"))

        obs = part_to_observation(
            trace_id=trace["trace_id"],
            session_id=sid,
            msg_id=row["msg_id"],
            msg_data=msg_data,
            part_id=row["part_id"],
            part_data=part_data,
            part_created=row.get("part_created"),
            part_updated=row.get("part_updated"),
            step_index=idx,
        )
        if obs is None:
            continue

        observations.append(obs)

        # Check for receipt marker in text/reasoning parts
        if not receipt:
            text = (part_data.get("text") or "")
            if isinstance(msg_data.get("content"), str):
                text = text or msg_data["content"]
            if text:
                receipt = extract_receipt(text)

    # Upsert
    n = upsert_observations(observations)
    summary["new_observations"] = n
    update_trace_observation_count(trace["trace_id"])

    # If receipt found, trigger completion graph
    if receipt:
        summary["receipt_found"] = True
        from backend.graph.state import ConductorState
        from backend.graph.graph import completion_graph

        state = ConductorState(
            task_id=trace["task_id"],
            trace_id=trace["trace_id"],
            project_id="<unknown>",
            session_id="<unknown>",
            agent_config_id=trace["agent_config_id"],
            input_spec=trace.get("input_spec") or {},
            output_spec=receipt,
            status="complete",
        )
        completion_graph.invoke(state)
        summary["completion_triggered"] = True

    return summary


# ──────────────────────── main loop ────────────────────────

def find_active_traces() -> list[dict]:
    """Traces that have a cli_session_id and are not yet terminal."""
    with pg_conn() as c, c.cursor() as cur:
        cur.execute(
            """
            SELECT trace_id, task_id, agent_config_id, cli_session_id, input_spec
              FROM traces
             WHERE cli_session_id IS NOT NULL
               AND status NOT IN ('complete', 'failed', 'abandoned')
            """
        )
        return cur.fetchall()


def main() -> int:
    if not OPENCODE_DB_PATH.is_file():
        print(f"[adapter] OpenCode DB not found: {OPENCODE_DB_PATH}")
        return 1

    traces = find_active_traces()
    if not traces:
        print("[adapter] no active traces")
        return 0

    print(f"[adapter] ingesting {len(traces)} active trace(s)")
    for t in traces:
        s = ingest_trace(t)
        print(
            f"  trace={s['trace_id']} "
            f"session_found={s['session_found']} "
            f"new_obs={s['new_observations']} "
            f"receipt={s['receipt_found']} "
            f"complete={s['completion_triggered']}"
        )

    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
