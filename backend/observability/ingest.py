"""Build Langfuse traces from the 3-source observability pipeline.

Sources:
  1. AionUi SQLite — conversation/message/team state
  2. OpenCode DB + structured logs — subagent token usage, model, cost
  3. Worktree filesystem/git — file-modified signals
"""
from __future__ import annotations

import os
import time
from typing import Any

import psycopg
from langfuse import Langfuse, propagate_attributes

from backend.aionui import AionUiReader
from backend.observability.langfuse_client import get_langfuse
from backend.observability.normalize import merge_events, compute_signal_snapshot


def _first_user(msgs: list[dict]) -> str:
    for m in msgs:
        if m.get("position") == "right":
            c = m.get("content", "")
            if isinstance(c, dict):
                return c.get("text", str(c))
            return str(c)
    return ""


def _last_assistant(msgs: list[dict]) -> str:
    for m in reversed(msgs):
        if m.get("position") == "left":
            c = m.get("content", "")
            if isinstance(c, dict):
                return c.get("text", str(c))
            return str(c)
    return ""


def _extract_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        return content.get("text", str(content))
    if isinstance(content, list):
        texts = []
        for item in content:
            if isinstance(item, dict):
                texts.append(item.get("text", str(item)))
            else:
                texts.append(str(item))
        return "\n".join(texts)
    return str(content)


def ingest_run(
    task_id: str,
    plan_id: str,
    agent_config: str,
    engine: str,
    model: str,
    conversation_id: str,
    reader: AionUiReader | None = None,
    db_path: str | None = None,
    extra_metadata: dict[str, Any] | None = None,
) -> str:
    """Build a Langfuse trace for a finished AionUi conversation.

    Returns the trace id.
    """
    if reader is None and db_path is None:
        raise ValueError("provide either reader or db_path")
    if reader is None:
        reader = AionUiReader(db_path)  # type: ignore[arg-type]

    msgs = reader.messages_for(conversation_id)

    lf: Langfuse = get_langfuse()

    meta = {
        "agent_config": agent_config,
        "engine": engine,
        "model": model,
        "conversation_id": conversation_id,
        "task_id": task_id,
        "plan_id": plan_id,
        "message_count": len(msgs),
        **(extra_metadata or {}),
    }

    with lf.start_as_current_observation(
        name=f"{agent_config}-{task_id[:8]}",
    ) as root:
        root.update(
            input={"user_query": _first_user(msgs)},
            output={"final_response": _last_assistant(msgs)},
        )

        with propagate_attributes(
            trace_name=f"{agent_config}-{task_id[:8]}",
            session_id=plan_id,
            tags=["conductor", engine, model],
            metadata={k: str(v) for k, v in meta.items() if isinstance(v, (str, int, float, bool))},
        ):
            for i, m in enumerate(msgs):
                msg_type = m.get("type", "text")
                with root.start_as_current_observation(
                    name=f"msg-{i}",
                ) as ob:
                    ob.update(
                        input={"role": m.get("position", "unknown")},
                        output={
                            "type": msg_type,
                            "content": _extract_text(m.get("content", "")),
                        },
                    )

            root.score(
                name="task_completion",
                value=1.0,
                data_type="NUMERIC",
                comment=f"ingested from AionUi conversation {conversation_id}",
            )

    trace_id = root.trace_id
    lf.flush()

    _update_aionui_links(conversation_id, trace_id)

    return trace_id


def ingest_full(
    task_id: str,
    plan_id: str,
    agent_config: str,
    engine: str,
    model: str,
    conversation_id: str,
    opencode_session_id: str | None = None,
    worktree_path: str | None = None,
    pid: int | None = None,
    reader: AionUiReader | None = None,
    aionui_db_path: str | None = None,
    extra_metadata: dict[str, Any] | None = None,
) -> str:
    """Build a Langfuse trace from all 3 sources with nested subagent spans.

    Unlike ``ingest_run`` which only reads AionUi, this function merges
    AionUi + OpenCode DB + worktree events into a single trace tree with
    token/cost per span, then writes a ``session_signals`` snapshot row.
    """
    events = merge_events(
        conversation_id=conversation_id,
        session_id=opencode_session_id or conversation_id,
        worktree_path=worktree_path,
        aionui_db=aionui_db_path,
    )

    lf: Langfuse = get_langfuse()
    trace_name = f"{agent_config}-{task_id[:8]}"

    with lf.start_as_current_observation(name=trace_name) as root:
        root.update(
            input={"user_intent": _first_user_ev(events)},
            output={"final_response": _last_assistant_ev(events)},
        )

        with propagate_attributes(
            trace_name=trace_name,
            session_id=plan_id,
            tags=["conductor", engine, model, "v4-full"],
            metadata={
                "task_id": task_id,
                "plan_id": plan_id,
                "agent_config": agent_config,
                "conversation_id": conversation_id,
                "opencode_session_id": opencode_session_id or "",
                "event_count": len(events),
                **(extra_metadata or {}),
            },
        ):
            _ingest_events_as_spans(root, events, lf)

            root.score(
                name="task_completion",
                value=1.0,
                data_type="NUMERIC",
                comment=f"ingested via v4 full pipeline; {len(events)} events",
            )

    trace_id = root.trace_id
    lf.flush()

    _update_aionui_links(conversation_id, trace_id)

    # Write signal snapshot
    sig = compute_signal_snapshot(events, worktree_path, pid)
    _write_session_signal(conversation_id, sig)

    return trace_id


def _first_user_ev(events: list[dict]) -> str:
    for ev in events:
        if ev.get("type") == "user_message":
            return ev.get("content", "")
    return ""


def _last_assistant_ev(events: list[dict]) -> str:
    for ev in reversed(events):
        if ev.get("type") == "assistant_message":
            return ev.get("content", "")
    return ""


def _ingest_events_as_spans(
    root: Any,
    events: list[dict],
    lf: Langfuse,
) -> None:
    """Create nested Langfuse observations from merged events."""
    span_map: dict[str, Any] = {}  # message_id -> observation

    for i, ev in enumerate(events):
        src = ev.get("source", "")
        etype = ev.get("type", "")
        meta = ev.get("metadata", {})
        tokens = ev.get("tokens", {})
        parent_id = ev.get("parent_id")

        span_name = f"{src}-{i}"
        with root.start_as_current_observation(name=span_name) as ob:
            ob.update(
                input={"type": etype, "source": src},
                output={
                    "content": (ev.get("content") or "")[:2000],
                    "tokens": tokens,
                },
            )
            mid = meta.get("message_id") or meta.get("session_id", "")
            if mid:
                span_map[mid] = ob

            # Attach token usage as span-level metadata if present
            if isinstance(tokens, dict) and any(tokens.values()):
                ob.update(
                    metadata={
                        "tokens_input": tokens.get("input", 0),
                        "tokens_output": tokens.get("output", 0),
                        "tokens_reasoning": tokens.get("reasoning", 0),
                        "tokens_cache_read": tokens.get("cache_read", 0),
                        "tokens_cache_write": tokens.get("cache_write", 0),
                        "model": meta.get("model", ""),
                        "provider": meta.get("provider", ""),
                        "agent": meta.get("agent", ""),
                        "cost": meta.get("cost", 0),
                    }
                )


def _write_session_signal(session_id: str, sig: dict) -> None:
    """Write a ``session_signals`` row to Conductor DB.

    Note: ``pid_alive`` is intentionally NOT written here — the ingest pipeline
    does not own the process lifecycle. The watcher (supervisor loop) owns PID
    tracking; it writes its own signal rows with the correct ``pid_alive`` value.
    The DB default for ``pid_alive`` is ``true`` so that ingest-origin rows don't
    cause false "crashed" verdicts.
    """
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        return

    try:
        with psycopg.connect(db_url) as c:
            with c.cursor() as cur:
                cur.execute(
                    """INSERT INTO session_signals
                       (session_id, ts, token_rate, last_activity,
                        terminal, quota_suspected, fs_changed)
                       VALUES (%s, to_timestamp(%s), %s, to_timestamp(%s),
                               %s, %s, %s)""",
                    (
                        session_id,
                        sig.get("ts", time.time()),
                        sig.get("token_rate", 0.0),
                        sig.get("last_activity", time.time()),
                        sig.get("terminal", False),
                        sig.get("quota_suspected", False),
                        sig.get("fs_changed", False),
                    ),
                )
            c.commit()
    except Exception:
        import traceback
        traceback.print_exc()


def _update_aionui_links(conversation_id: str, trace_id: str) -> None:
    """Write the Langfuse trace id back to aionui_links.

    Only updates existing rows — does not insert (requires a valid task_id FK).
    The caller (orchestration layer) is responsible for creating the aionui_links
    row with a real task_id before calling ingest_run.
    """
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        return

    with psycopg.connect(db_url) as c:
        with c.cursor() as cur:
            cur.execute(
                """UPDATE aionui_links
                   SET langfuse_trace_id = %s, status = 'ingested'
                   WHERE aionui_conversation_id = %s""",
                (trace_id, conversation_id),
            )
        c.commit()
