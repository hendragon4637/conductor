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
        }

    placeholders = ",".join("?" for _ in conv_ids)
    conn = _ro(db_path)
    try:
        rows = conn.execute(LATEST_PER_CONV.format(placeholders=placeholders), conv_ids).fetchall()
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

    return {
        "have_data": bool(row_dicts),
        "any_error": any_error,
        "error_codes": error_codes,
        "last_activity_ms": last_activity_ms,
        "age_s": ((now_ms - last_activity_ms) / 1000.0) if last_activity_ms else None,
        "terminal": False,
        "latest_sig": latest_sig,
        "rows": row_dicts,
    }
