"""Source adapter: AionUi SQLite → normalized events.

Wraps the existing AionUiReader and yields normalized Event dicts
for the observability pipeline.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Iterator

from backend.aionui.reader import AionUiReader


def aionui_events(
    conversation_id: str,
    db_path: str | None = None,
    reader: AionUiReader | None = None,
) -> Iterator[dict]:
    """Yield normalized event dicts from an AionUi conversation.

    Each event has:
        ts (float): unix timestamp
        source (str): "aionui"
        type (str): "user_message" | "assistant_message" | "team_event"
        role (str | None)
        content (str)
        metadata (dict)
    """
    if reader is None:
        if db_path is None:
            db_path = str(
                Path.home() / ".config" / "AionUi" / "aionui" / "aionui-backend.db"
            )
        reader = AionUiReader(db_path)

    msgs = reader.messages_for(conversation_id)

    for m in msgs:
        position = m.get("position", "unknown")
        role = "user" if position == "right" else ("assistant" if position == "left" else None)
        content = _extract_text(m.get("content", ""))
        created = m.get("created_at", m.get("createdAt", 0))
        if isinstance(created, str):
            try:
                created = time.mktime(time.strptime(created, "%Y-%m-%dT%H:%M:%S"))
            except (ValueError, TypeError):
                created = time.time()

        yield {
            "ts": created,
            "source": "aionui",
            "type": "user_message" if role == "user" else "assistant_message",
            "role": role,
            "content": content,
            "metadata": {
                "message_id": m.get("id", ""),
                "msg_type": m.get("type", "text"),
                "conversation_id": conversation_id,
            },
        }

    # Also emit conversation-level metadata
    convs = reader.conversations(limit=100)
    for conv in convs:
        if conv.get("id") == conversation_id:
            yield {
                "ts": time.time(),
                "source": "aionui",
                "type": "conversation_meta",
                "role": None,
                "content": "",
                "metadata": {
                    "conversation_id": conversation_id,
                    "title": conv.get("title", ""),
                    "status": conv.get("status", ""),
                    "team_id": conv.get("team_id", ""),
                },
            }
            break


def _extract_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        return content.get("text", json.dumps(content, default=str))
    if isinstance(content, list):
        texts = []
        for item in content:
            if isinstance(item, dict):
                texts.append(item.get("text", json.dumps(item, default=str)))
            else:
                texts.append(str(item))
        return "\n".join(texts)
    return str(content)
