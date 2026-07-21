"""SignalSource abstraction layer — signal data model, verdict derivation, registry."""
from __future__ import annotations

import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar

from backend.db import queries
from backend.watcher.signals_query import node_signal, AIONUI_DB


# ── Constants ──────────────────────────────────────────────────────────────

STALL_SECS = 300

TERMINAL_VERDICTS: frozenset[str] = frozenset({
    "done",
    "done_no_change",
    "failed",
    "crashed",
})


# ── Data model ─────────────────────────────────────────────────────────────

@dataclass
class RawSignal:
    terminal: bool = False
    any_error: bool = False
    quota_suspected: bool = False
    fs_changed: bool = False
    last_activity_ts: float | None = None
    agent_alive: bool = False
    detail: dict[str, Any] = field(default_factory=dict)


# ── Verdict derivation ─────────────────────────────────────────────────────

def derive_verdict(sig: RawSignal, now: float) -> str:
    if sig.any_error and not sig.terminal:
        return "crashed"
    if sig.any_error and sig.terminal:
        return "failed"
    if sig.quota_suspected:
        return "quota"
    if sig.terminal and sig.fs_changed:
        return "done"
    if sig.terminal and not sig.fs_changed:
        return "done_no_change"
    if sig.last_activity_ts is not None and (now - sig.last_activity_ts) > STALL_SECS:
        return "stalled"
    return "running"


# ── SignalSource ABC ───────────────────────────────────────────────────────

class SignalSource(ABC):
    backend: ClassVar[str]

    @abstractmethod
    def query(self, node_session) -> RawSignal:
        ...


# ── Helpers ────────────────────────────────────────────────────────────────

def _run_git_diff(worktree_path: str | None) -> str:
    if not worktree_path:
        return ""
    try:
        from contracts.paths import git_pathspec_excludes

        excludes = git_pathspec_excludes()
        cmd = ["git", "diff", "--stat", "--", "."] + excludes
        result = subprocess.run(
            cmd,
            cwd=worktree_path,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.stdout
    except Exception:
        return ""


def _resolve_session_conv_ids(session_id: str, node_id: str | None) -> list[str]:
    """Resolve AionUi conversation IDs from the DB for a session+node."""
    if not node_id:
        return []
    try:
        with queries.conn() as c, c.cursor() as cur:
            cur.execute(
                """SELECT l.aionui_conversation_id
                   FROM tasks t
                   JOIN aionui_links l ON l.task_id = t.task_id
                   WHERE t.session_id = %s AND t.node_id = %s
                   ORDER BY l.created_at ASC""",
                (session_id, node_id),
            )
            return [str(dict(row)["aionui_conversation_id"]) for row in cur.fetchall()]
    except Exception:
        return []


# ── AionUi SignalSource ────────────────────────────────────────────────────

class AionUiSignalSource(SignalSource):
    backend = "opencode"

    def query(self, node_session) -> RawSignal:
        # Support both dict and object access
        if isinstance(node_session, dict):
            conv_id = node_session.get("aionui_conversation_id") or node_session.get("conversation_id")
            worktree = node_session.get("worktree")
            session_id = node_session.get("session_id")
            node_id = node_session.get("node_id")
        else:
            conv_id = getattr(node_session, "aionui_conversation_id", None) or getattr(node_session, "conversation_id", None)
            worktree = getattr(node_session, "worktree", None)
            session_id = getattr(node_session, "session_id", None)
            node_id = getattr(node_session, "node_id", None)

        conv_ids: list[str] = []
        if conv_id:
            conv_ids = [conv_id]
        elif session_id:
            conv_ids = _resolve_session_conv_ids(session_id, node_id)

        if not conv_ids:
            return RawSignal(
                fs_changed=_run_git_diff(worktree) != "",
                detail={"src": "aionui", "reason": "no conversation"},
            )

        qsig = node_signal(AIONUI_DB, conv_ids)
        fs_dirty = _run_git_diff(worktree) != ""
        last_activity_ms = qsig.get("last_activity_ms", 0)
        last_activity_ts = (last_activity_ms / 1000.0) if last_activity_ms else None

        return RawSignal(
            terminal=qsig.get("terminal", False),
            any_error=qsig.get("any_error", False),
            fs_changed=fs_dirty,
            last_activity_ts=last_activity_ts,
            agent_alive=qsig.get("agent_alive", False),
            detail={
                "src": "aionui",
                "conv_ids": conv_ids,
                "error_codes": qsig.get("error_codes", []),
                "have_data": qsig.get("have_data", False),
                "latest_sig": qsig.get("latest_sig"),
                "acp_session_statuses": qsig.get("acp_session_statuses", {}),
                "conv_statuses": qsig.get("conv_statuses", {}),
                "agent_alive": qsig.get("agent_alive", False),
            },
        )


# ── Hermes SignalSource (direct HTTP) ───────────────────────────────────────

class HermesSignalSource(SignalSource):
    """Polls the Hermes HTTP API (``/v1/runs/{run_id}``) for run status.

    Expects ``node_session.backend_ref`` to hold the Hermes ``run_id``
    (stored as ``aionui_team_id`` in the DB by ``launch_run()``).
    """

    backend = "hermes"

    def query(self, node_session) -> RawSignal:
        # The run_id is stored in aionui_team_id by launch_run()
        if isinstance(node_session, dict):
            run_id = node_session.get("aionui_team_id")
            worktree = node_session.get("worktree")
        else:
            run_id = getattr(node_session, "aionui_team_id", None)
            worktree = getattr(node_session, "worktree", None)

        if not run_id:
            return RawSignal(
                fs_changed=_run_git_diff(worktree) != "",
                detail={"src": "hermes", "reason": "no run_id"},
            )

        from backend.hermes_adapter import HermesClient

        client = HermesClient()
        try:
            status_resp = client.get_run_status(run_id)
        except RuntimeError:
            return RawSignal(
                any_error=True,
                fs_changed=_run_git_diff(worktree) != "",
                detail={"src": "hermes", "run_id": run_id, "error": "poll_failed"},
            )

        status = status_resp.get("status", "unknown")
        terminal = status in ("completed", "failed", "cancelled", "stopped")
        any_error = status in ("failed", "cancelled", "stopped", "error")
        fs_dirty = _run_git_diff(worktree) != ""

        return RawSignal(
            terminal=terminal,
            any_error=any_error,
            fs_changed=fs_dirty,
            last_activity_ts=None,  # Hermes doesn't expose per-second activity
            detail={
                "src": "hermes",
                "run_id": run_id,
                "status": status,
            },
        )


# ── Registry ───────────────────────────────────────────────────────────────

SIGNAL_SOURCES: dict[str, SignalSource] = {
    "opencode": AionUiSignalSource(),
    "opencode_omo": AionUiSignalSource(),
    "hermes": HermesSignalSource(),
}
