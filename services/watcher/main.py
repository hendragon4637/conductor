"""watcher-svc entrypoint.

FastAPI /health endpoint, background watch loop that polls ``node_sessions``
with ``verdict=NULL``, derives terminal verdicts via backend.watcher signal
sources, and emits ``NodeObserved`` via the transactional outbox.

Watches are settle-time gated (stable for >= 2 consecutive polls AND quiet
for >= 30 s) before producing a terminal verdict, matching the monolith's
watcher convention.  Per-session state is kept in-process and bootstrapped
from the DB on startup.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI

from contracts.events import NodeObserved, NodeSpawned
from shared.bus import EventBus
from shared.config import ServiceConfig
from shared.db import init_db, session as db_session
from shared.models import NodeSession
from shared.outbox import emit

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(_h)

cfg = ServiceConfig.from_env()
bus = EventBus(cfg)

POLL_INTERVAL = int(os.environ.get("WATCHER_POLL_INTERVAL", "90"))

_SETTLE_S_PLANNING = int(os.environ.get("WATCHER_SETTLE_S_PLANNING", "180"))
_STABLE_POLLS_PLANNING = int(os.environ.get("WATCHER_STABLE_POLLS_PLANNING", "5"))
_SETTLE_S_EXECUTION = int(os.environ.get("WATCHER_SETTLE_S_EXECUTION", "60"))
_STABLE_POLLS_EXECUTION = int(os.environ.get("WATCHER_STABLE_POLLS_EXECUTION", "5"))


_ROLE_CONFIG: dict[str, dict[str, int]] = {
    "planning": {"settle_s": _SETTLE_S_PLANNING, "stable_polls": _STABLE_POLLS_PLANNING},
    "execution": {"settle_s": _SETTLE_S_EXECUTION, "stable_polls": _STABLE_POLLS_EXECUTION},
}


# ── Per-session tracking state ──────────────────────────────────────────────


class SessionTrack:
    """In-memory per-session state for settle-time detection."""

    def __init__(self, node_session_id: str, worktree: str | None = None, role: str = "execution"):
        self.node_session_id = node_session_id
        self.worktree = worktree
        self.role = role
        cfg = _ROLE_CONFIG.get(role, _ROLE_CONFIG["execution"])
        self.settle_s: int = cfg["settle_s"]
        self.stable_polls_threshold: int = cfg["stable_polls"]
        self.saw_change: bool = False
        self.saw_fs_change: bool = False  # file-only changes (planning gate)
        self.last_git_sig: str | None = None
        self.last_query_sig: str | None = None
        self.last_change_ts: float | None = None
        self.unchanged_cycles: int = 0
        self.started_ts: float = time.time()


_tracker: dict[str, SessionTrack] = {}


def _is_spawned(ns: NodeSession) -> bool:
    """Check whether a node_session has a backend reference (has been spawned).

    Pending nodes that haven't been spawned yet have neither backend ref set.
    We skip them to avoid false settle-time verdicts on empty worktrees.
    """
    return bool(ns.aionui_conversation_id) or bool(ns.aionui_team_id)


def _bootstrap_tracker() -> None:
    """Load active sessions from DB into the tracker on startup.

    Filters out stale sessions from old runs (>48h) to prevent watcher
    from polling dead node_sessions left behind by previous runs.
    """
    try:
        from datetime import timedelta
        from sqlalchemy import or_
        with db_session() as s:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=48)
            active = (
                s.query(NodeSession)
                .filter(
                    NodeSession.created_at >= cutoff,
                    or_(
                        NodeSession.verdict.is_(None),
                        NodeSession.verdict.in_(["running", "pending"]),
                    ),
                )
                .all()
            )
        bootstrapped = 0
        for ns in active:
            if not _is_spawned(ns):
                continue
            if ns.id not in _tracker:
                git_sig = _git_state_signature(ns.worktree)
                _tracker[ns.id] = SessionTrack(
                    node_session_id=ns.id,
                    worktree=ns.worktree,
                    role=getattr(ns, "role", "execution"),
                )
                _tracker[ns.id].last_git_sig = git_sig
                _tracker[ns.id].last_change_ts = time.time()
                bootstrapped += 1
        logger.info("Bootstrapped %d active session(s) into tracker", bootstrapped)
    except Exception:
        logger.exception("Failed to bootstrap tracker")


# ── Helpers (copied from monolith supervisor for settle-time) ────────────────


def _git_state_signature(worktree_path: str | None) -> str | None:
    if not worktree_path or not os.path.isdir(worktree_path):
        return None
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=worktree_path,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        return hashlib.sha1(result.stdout.encode("utf-8", errors="ignore")).hexdigest()
    except Exception:
        return None


def _pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False


def _persist_signal(node_session_id: str, sig: dict[str, Any]) -> None:
    """Write signal snapshot to session_signals table."""
    from sqlalchemy import text
    try:
        with db_session() as s:
            s.execute(
                text("""
                    INSERT INTO session_signals
                        (session_id, ts, token_rate, last_activity, terminal,
                         quota_suspected, pid_alive, fs_changed,
                         any_error, error_codes, age_s, watcher_node_id, signal_snapshot)
                    VALUES
                        (:sid, NOW(), :token_rate, TO_TIMESTAMP(:last_activity), :terminal,
                         :quota_suspected, :pid_alive, :fs_changed,
                         :any_error, CAST(:error_codes AS jsonb), :age_s, :node_id, CAST(:snapshot AS jsonb))
                """),
                {
                    "sid": node_session_id,
                    "token_rate": sig.get("token_rate", 0.0),
                    "last_activity": sig.get("last_activity", time.time()),
                    "terminal": sig.get("terminal", False),
                    "quota_suspected": sig.get("quota_suspected", False),
                    "pid_alive": sig.get("pid_alive", True),
                    "fs_changed": sig.get("fs_changed", False),
                    "any_error": sig.get("any_error", False),
                    "error_codes": json.dumps(sig.get("error_codes", [])),
                    "age_s": sig.get("age_s"),
                    "node_id": sig.get("watcher_node_id"),
                    "snapshot": json.dumps(sig),
                },
            )
            s.commit()
    except Exception:
        logger.exception("Failed to persist signal for %s", node_session_id)


# ── Consumer handlers ────────────────────────────────────────────────────────


def _handle_node_spawned(session, payload):
    ns_id = payload.get("node_session_id")
    worktree = payload.get("worktree")
    logger.info("Node spawned: %s (backend=%s)", ns_id, payload.get("backend"))
    if ns_id and ns_id not in _tracker:
        row = session.query(NodeSession).filter(NodeSession.id == ns_id).first()
        role = getattr(row, "role", "execution") if row else "execution"
        st = SessionTrack(node_session_id=ns_id, worktree=worktree, role=role)
        st.last_git_sig = _git_state_signature(worktree)
        st.last_change_ts = time.time()
        _tracker[ns_id] = st


# ── Watch loop ───────────────────────────────────────────────────────────────


def _watch_loop() -> None:
    from backend.watcher.signals import SIGNAL_SOURCES, derive_verdict as derive_from_signals
    from backend.watcher.signals_query import node_signal, AIONUI_DB

    print(f"[PRINT] Watch loop started (interval={POLL_INTERVAL}s, "
          f"planning_settle={_SETTLE_S_PLANNING}s, execute_settle={_SETTLE_S_EXECUTION}s)", flush=True)
    logger.info("Watch loop started (interval=%ds, planning_settle=%ds, execute_settle=%ds)",
                POLL_INTERVAL, _SETTLE_S_PLANNING, _SETTLE_S_EXECUTION)

    _bootstrap_tracker()
    print("[PRINT] bootstrap_tracker() done", flush=True)

    while True:
        try:
            from sqlalchemy import or_
            with db_session() as s:
                active: list[NodeSession] = (
                    s.query(NodeSession)
                    .filter(or_(
                        NodeSession.verdict.is_(None),
                        NodeSession.verdict.in_(["running", "pending"]),
                    ))
                    .all()
                )
        except Exception:
            logger.exception("Failed to query active node_sessions")
            time.sleep(POLL_INTERVAL)
            continue

        if not active:
            print(f"[PRINT] No active sessions, sleeping {POLL_INTERVAL}s", flush=True)
            time.sleep(POLL_INTERVAL)
            continue

        print(f"[PRINT] Polling {len(active)} active session(s)", flush=True)
        for ns in active:
            if not _is_spawned(ns):
                continue  # not yet spawned — skip until DAG advancement creates it
            try:
                # Ensure tracker entry exists
                if ns.id not in _tracker:
                    st = SessionTrack(
                        node_session_id=ns.id,
                        worktree=ns.worktree,
                        role=getattr(ns, "role", "execution"),
                    )
                    st.last_git_sig = _git_state_signature(ns.worktree)
                    st.last_change_ts = time.time()
                    _tracker[ns.id] = st

                st = _tracker[ns.id]
                st.worktree = ns.worktree

                # ── Settle-time check (matches monolith _poll_state) ──
                now = time.time()
                git_sig = _git_state_signature(ns.worktree)
                fs_changed = git_sig != st.last_git_sig

                # Query signal source for backend-level activity
                src = SIGNAL_SOURCES.get(ns.backend)
                if src is None:
                    logger.warning("No signal source for backend=%s (ns=%s)", ns.backend, ns.id)
                    continue

                raw = src.query(ns)
                v_signal = derive_from_signals(raw, time.time())
                qsig = {
                    "latest_sig": raw.detail.get("latest_sig", ""),
                    "have_data": raw.terminal or bool(raw.last_activity_ts),
                    "any_error": raw.any_error,
                    "error_codes": raw.detail.get("error_codes", []),
                    "last_activity_ms": (raw.last_activity_ts or 0) * 1000,
                    "age_s": raw.detail.get("age_s"),
                }
                query_sig = qsig.get("latest_sig")
                query_changed = query_sig != st.last_query_sig

                if fs_changed:
                    st.last_git_sig = git_sig
                    st.saw_fs_change = True
                if query_changed:
                    st.last_query_sig = query_sig

                if fs_changed or query_changed:
                    st.last_change_ts = now
                    st.saw_change = True
                    st.unchanged_cycles = 0
                elif raw.agent_alive and not raw.any_error:
                    # Composite liveness: agent is alive (ACP runtime running,
                    # conversation active, or executing a tool call) even without
                    # new messages or files — prevent false stall detection.
                    st.unchanged_cycles = 0
                else:
                    st.unchanged_cycles += 1

                role = getattr(ns, "role", "execution")
                quiet_for = (now - st.last_change_ts) if st.last_change_ts else None
                stable_polls = st.unchanged_cycles >= st.stable_polls_threshold

                if role == "planning":
                    # Planning: use derive_verdict as gate against premature terminal.
                    # Agent may explore/think for long periods without writing files.
                    # Only terminal via settle if files were written (saw_fs_change),
                    # or if the conversation is truly stalled/errored via derive_verdict.
                    if v_signal in ("failed", "crashed", "quota"):
                        terminal = True
                    elif v_signal == "stalled":
                        terminal = True
                    elif (v_signal == "running" and st.saw_fs_change
                          and stable_polls and quiet_for is not None
                          and quiet_for >= st.settle_s
                          and not fs_changed and not query_changed):
                        terminal = True
                    else:
                        terminal = False
                else:
                    # Execution: settle-time + v_signal terminal detection.
                    # Respect derive_verdict signals for stalled/error states,
                    # and use settle-time only when changes were observed.
                    if v_signal in ("failed", "crashed", "quota"):
                        terminal = True
                    elif v_signal == "stalled":
                        terminal = True
                    else:
                        terminal = bool(
                            stable_polls
                            and quiet_for is not None
                            and quiet_for >= st.settle_s
                            and not fs_changed
                            and not query_changed
                            and (st.saw_change or qsig.get("have_data", False))
                        )

                last_activity = (qsig.get("last_activity_ms", 0) / 1000.0) or st.last_change_ts or st.started_ts

                # Build signal snapshot for persistence
                sig_snapshot = {
                    "pid_alive": _pid_alive(None),
                    "terminal": terminal,
                    "quota_suspected": False,
                    "token_rate": 1.0 if fs_changed else 0.0,
                    "fs_changed": fs_changed,
                    "last_activity": last_activity,
                    "any_error": qsig.get("any_error", False),
                    "error_codes": qsig.get("error_codes", []),
                    "age_s": qsig.get("age_s"),
                    "watcher_node_id": ns.node_id,
                    "unchanged_cycles": st.unchanged_cycles,
                    "quiet_for": quiet_for,
                    "stable_polls": stable_polls,
                    "v_signal": v_signal,
                    "role": role,
                    "saw_fs_change": st.saw_fs_change,
                }

                # If not terminal and no error, skip to next
                if not terminal and not qsig.get("any_error", False):
                    _persist_signal(ns.id, sig_snapshot)
                    continue

                # Terminal or error: derive final verdict
                if terminal:
                    # Use saw_fs_change for both roles — if files were ever
                    # written to the worktree, the agent produced output.
                    if st.saw_fs_change:
                        verdict_str = "done"
                    else:
                        verdict_str = "done_no_change"
                else:
                    verdict_str = "failed"

                # ── Write verdict + emit event ──
                # Only skip if verdict is already terminal — active verdicts
                # (running/pending) from the executor are fair game.
                TERMINAL_VERDICTS = {"done", "done_no_change", "failed", "crashed", "quota"}
                with db_session() as tx:
                    row = tx.query(NodeSession).filter(NodeSession.id == ns.id).first()
                    if row is None or row.verdict in TERMINAL_VERDICTS:
                        _tracker.pop(ns.id, None)
                        continue

                    row.verdict = verdict_str
                    row.finished_at = datetime.now(timezone.utc)

                    emit(tx, NodeObserved(
                        node_session_id=row.id,
                        verdict=verdict_str,
                        fs_changed=fs_changed,
                        ts=time.time(),
                    ))
                    tx.commit()

                _persist_signal(ns.id, {**sig_snapshot, "terminal": True})
                _tracker.pop(ns.id, None)

                print(
                    f"[PRINT] verdict ns={ns.id} verdict={verdict_str} "
                    f"fs_changed={fs_changed} cycles={st.unchanged_cycles}",
                    flush=True,
                )
                logger.info(
                    "Verdict %s: %s (cycles=%d, quiet=%.1fs)",
                    ns.id, verdict_str, st.unchanged_cycles, quiet_for or 0,
                )

            except Exception:
                logger.exception("Error polling node_session %s", ns.id)

        time.sleep(POLL_INTERVAL)


# ── Lifespan ─────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: init DB, declare Rabbit topology, start consumers + relay."""
    init_db(cfg)
    bus.declare()
    bus.start_consumer("watcher.q", _handle_node_spawned, "watcher-svc")

    relay_t = threading.Thread(target=bus.relay_loop, daemon=True)
    relay_t.start()

    consumer_t = threading.Thread(target=bus.start_consuming, daemon=True)
    consumer_t.start()

    watch_t = threading.Thread(target=_watch_loop, daemon=True)
    watch_t.start()

    print("[PRINT] watcher-svc ready — threads started", flush=True)
    logger.info("watcher-svc ready")
    yield
    bus.close()


# ── FastAPI app ──────────────────────────────────────────────────────────────

app = FastAPI(title="watcher-svc", version="0.1.0", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok", "service": "watcher"}


# ── API: active verdicts ─────────────────────────────────────────────────────


@app.get("/verdicts")
def list_verdicts():
    """Return current verdict status for all watched sessions."""
    return {
        "active_count": len(_tracker),
        "sessions": {
            nid: {
                "worktree": st.worktree,
                "unchanged_cycles": st.unchanged_cycles,
                "saw_change": st.saw_change,
                "uptime_s": round(time.time() - st.started_ts, 1),
            }
            for nid, st in _tracker.items()
        },
    }


# ── Main ─────────────────────────────────────────────────────────────────────


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("WATCHER_PORT", "8092"))
    uvicorn.run(app, host="0.0.0.0", port=port)
