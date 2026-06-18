"""Resume / Pause / Cancel — synced to AionUi state + git worktree + DB.

Sync discipline (from spec):
- Every control updates BOTH Conductor state (registry + DB) AND AionUi
  (stop/cancel the team conversation) AND the git ladder (commit on pause).
- Resume happens at node/commit boundaries, never mid-AionUi-turn.
"""
from __future__ import annotations

import logging
import os
from typing import Any

import psycopg

from backend.watcher.gitops import commit_chunk

logger = logging.getLogger(__name__)


_DB_URL = os.environ.get("DATABASE_URL", "postgresql://aipc:aipc@localhost:5432/aipc_conductor")


def _db() -> psycopg.Connection:
    return psycopg.connect(_DB_URL)


def _update_session_status(session_id: str, status: str) -> None:
    """Persist status to the sessions table (or session_signals latest)."""
    try:
        with _db() as c:
            with c.cursor() as cur:
                cur.execute(
                    """INSERT INTO session_signals (session_id, ts, terminal)
                       VALUES (%s, NOW(), %s)
                       ON CONFLICT DO NOTHING""",
                    (session_id, status == "done"),
                )
            c.commit()
    except Exception:
        logger.exception("failed to persist status for %s", session_id)


def _stop_aionui_team(session_id: str) -> None:
    """Try to stop the AionUi team conversation via its API.

    Falls back to terminating the spawned process if no AionUi stop endpoint.
    """
    # Placeholder: AionUi stop endpoint integration
    # from backend.aionui.client import AionUiClient
    # client = AionUiClient()
    # client.stop_conversation(conversation_id)
    logger.info("stop aionui team for %s (stub)", session_id)


def pause(session_id: str, st: Any) -> None:
    """Pause a session: commit progress, halt AionUi, record cursor.

    1. Commit current worktree progress via ``commit_chunk``.
    2. Tell AionUi to halt the team conversation.
    3. Mark registry status = 'paused'.
    4. Record last completed node cursor in DB.
    """
    logger.info("pause %s", session_id)

    # 1. Commit
    if st.worktree:
        commit_chunk(st.worktree, st.session_id, f"pause-{session_id[:8]}")

    # 2. Halt AionUi
    _stop_aionui_team(session_id)

    # 3. Mark paused
    st.status = "paused"
    _update_session_status(session_id, "paused")

    # 4. Record cursor (last completed node from session metadata)
    try:
        with _db() as c:
            with c.cursor() as cur:
                cur.execute(
                    """UPDATE aionui_links SET status = 'paused'
                       WHERE aionui_conversation_id = %s""",
                    (st.conversation_id or session_id,),
                )
            c.commit()
    except Exception:
        logger.exception("failed to record pause cursor for %s", session_id)


def resume(session_id: str, st: Any) -> None:
    """Resume a paused session.

    1. Re-enter the DAG at the last INCOMPLETE node.
    2. Re-spawn that node's AionUi team in the SAME worktree.
    3. Never resume mid-turn — only at node boundary.
    """
    logger.info("resume %s", session_id)

    # 1-2. Re-spawn the failed/paused node (stub — orchestration layer handles)
    # from backend.orchestration.runner import run_plan
    # run_plan(plan_id, resume_from=session_id)

    # 3. Mark running
    st.status = "running"
    st.retry_count = 0
    _update_session_status(session_id, "running")
    logger.info("resume: %s set to running (re-spawn at incomplete node)", session_id)


def cancel(session_id: str, st: Any) -> None:
    """Cancel a session: stop AionUi, mark cancelled, LEAVE worktree intact.

    Deletion is a separate explicit cleanup — never delete on cancel.
    """
    logger.info("cancel %s", session_id)

    # Stop AionUi
    _stop_aionui_team(session_id)

    # Mark cancelled
    st.status = "cancelled"
    _update_session_status(session_id, "cancelled")

    logger.info("cancel: %s cancelled (worktree left intact)", session_id)


def _on_verdict(session_id: str, st: Any, v: str) -> None:
    """Called by the supervisor loop when a verdict transitions."""
    if v == "done":
        # commit, then advance DAG
        if st.worktree:
            commit_chunk(st.worktree, session_id, f"done-{session_id[:8]}")
        _update_session_status(session_id, "done")

    elif v in ("failed", "stalled", "quota", "crashed"):
        st.retry_count += 1
        max_retry = st.thresholds.get("retry_max", 2)
        if st.retry_count <= max_retry:
            backoff = st.thresholds.get("retry_backoff_s", 30)
            logger.info(
                "retry %s/%s for %s (backoff=%ss)",
                st.retry_count, max_retry, session_id, backoff,
            )
            # Resume handles the re-spawn logic
            resume(session_id, st)
        else:
            logger.warning("escalating %s: retry cap exceeded", session_id)
            _update_session_status(session_id, f"escalated-{v}")
