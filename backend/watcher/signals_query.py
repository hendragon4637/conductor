from __future__ import annotations

import hashlib
import os
import sqlite3
import time
from typing import Any


AIONUI_DB = os.environ.get(
    "AIONUI_DB",
    "/home/aipc/.config/AionUi/aionui/aionui-backend.db",
)


LATEST_PER_CONV = """
SELECT m.conversation_id, m.type, m.status, m.position, m.created_at,
       json_extract(m.content, '$.error.code') AS err_code
FROM messages m
JOIN (
    SELECT conversation_id, MAX(created_at) AS mx
    FROM messages
    WHERE conversation_id IN ({placeholders})
    GROUP BY conversation_id
) last
  ON m.conversation_id = last.conversation_id AND m.created_at = last.mx
"""

ACP_SESSION_STATUS = """
SELECT conversation_id, session_status, last_active_at
FROM acp_session
WHERE conversation_id IN ({placeholders})
"""

CONV_STATUS = """
SELECT id, status
FROM conversations
WHERE id IN ({placeholders})
"""

ACTIVE_TOOL_CALLS = """
SELECT COUNT(*)
FROM messages
WHERE conversation_id IN ({placeholders})
  AND status = 'work'
  AND type = 'acp_tool_call'
"""


def _ro(db_path: str) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def node_signal(db_path: str, conv_ids: list[str]) -> dict[str, Any]:
    if not conv_ids:
        return {
            "have_data": False,
            "any_error": False,
            "error_codes": [],
            "last_activity_ms": 0,
            "age_s": None,
            "terminal": False,
            "latest_sig": None,
            "rows": [],
            "acp_session_statuses": {},
            "conv_statuses": {},
            "active_tool_call_count": 0,
            "agent_alive": False,
        }

    placeholders = ",".join("?" for _ in conv_ids)
    conn = _ro(db_path)
    try:
        rows = conn.execute(LATEST_PER_CONV.format(placeholders=placeholders), conv_ids).fetchall()
        # Composite liveness queries
        acp_rows = conn.execute(
            ACP_SESSION_STATUS.format(placeholders=placeholders), conv_ids
        ).fetchall()
        conv_rows = conn.execute(
            CONV_STATUS.format(placeholders=placeholders), conv_ids
        ).fetchall()
        tool_rows = conn.execute(
            ACTIVE_TOOL_CALLS.format(placeholders=placeholders), conv_ids
        ).fetchall()
    finally:
        conn.close()

    now_ms = int(time.time() * 1000)
    row_dicts = [
        {
            "conversation_id": row[0],
            "type": row[1],
            "status": row[2],
            "position": row[3],
            "created_at": row[4],
            "err_code": row[5],
        }
        for row in rows
    ]
    any_error = any((row["status"] == "error") or bool(row["err_code"]) for row in row_dicts)
    error_codes = [str(row["err_code"]) for row in row_dicts if row["err_code"]]
    last_activity_ms = max((int(row["created_at"] or 0) for row in row_dicts), default=0)
    latest_sig = None
    if row_dicts:
        latest_sig = hashlib.sha1(
            "|".join(
                f"{row['conversation_id']}:{row['created_at']}:{row['type']}:{row['status']}:{row['position']}:{row['err_code']}"
                for row in sorted(row_dicts, key=lambda item: (str(item["conversation_id"]), int(item["created_at"] or 0)))
            ).encode("utf-8")
        ).hexdigest()

    # Composite liveness: agent is alive if ACP runtime is running,
    # conversation is running, or agent is actively executing a tool call.
    acp_statuses: dict[str, str] = {str(r[0]): str(r[1]) for r in acp_rows}
    conv_statuses: dict[str, str] = {str(r[0]): str(r[1]) for r in conv_rows}
    active_tool_count = sum(r[0] for r in tool_rows) if tool_rows else 0

    agent_alive = (
        any(v == "running" for v in acp_statuses.values())
        or any(v == "running" for v in conv_statuses.values())
        or active_tool_count > 0
    )

    return {
        "have_data": bool(row_dicts),
        "any_error": any_error,
        "error_codes": error_codes,
        "last_activity_ms": last_activity_ms,
        "age_s": ((now_ms - last_activity_ms) / 1000.0) if last_activity_ms else None,
        "terminal": False,
        "latest_sig": latest_sig,
        "rows": row_dicts,
        "acp_session_statuses": acp_statuses,
        "conv_statuses": conv_statuses,
        "active_tool_call_count": active_tool_count,
        "agent_alive": agent_alive,
    }
