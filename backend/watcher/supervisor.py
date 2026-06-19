"""Singleton supervisor loop + session registry.

Rules (locked from spec):
- Watcher = deterministic function, NOT an LLM agent.
- One singleton loop with a per-session registry, NOT one watcher per session.
- Reads AionUi/CLI/FS directly (ground truth) — never trusts AionUi self-reported status.
- Event-driven primary + interval polling safety net.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import json
import subprocess
import time
from typing import Any

from backend.aionui import AionUiClient
from backend.builtins.git_ops import commit_node
from backend.builtins.handoff import build_node_context
from backend.db import queries
from backend.evaluator.gate import evaluate_gate
from backend.evaluator.l2_judge import JudgeUnavailableError, run_l2
from backend.evaluator.remediation import insert_remediation
from backend.orchestration.spawn import spawn_node_team
from backend.planning.store import save_node_session
from backend.watcher.signals_query import node_signal, AIONUI_DB
from backend.watcher.verdict import verdict, VERDICT_RUNNING
from backend.worktree import WorktreeManager

logger = logging.getLogger(__name__)

# Default per-session thresholds (can be overridden per role)
DEFAULT_THRESHOLDS = {
    "stall_s": 180,
    "settle_s": 30,
    "retry_max": 2,
    "retry_backoff_s": 30,
}


class SessionState:
    """Mutable state tracked per session by the singleton watcher."""

    def __init__(
        self,
        session_id: str,
        pid: int | None = None,
        worktree: str | None = None,
        thresholds: dict[str, Any] | None = None,
        conversation_id: str | None = None,
        opencode_session_id: str | None = None,
        node_id: str | None = None,
        plan_id: str | None = None,
        project_id: str | None = None,
        node_session_id: str | None = None,
    ):
        self.session_id = session_id
        self.pid = pid
        self.worktree = worktree
        self.conversation_id = conversation_id
        self.opencode_session_id = opencode_session_id
        self.node_id = node_id
        self.plan_id = plan_id
        self.project_id = project_id
        self.node_session_id = node_session_id
        self.thresholds = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
        self.status: str = VERDICT_RUNNING
        self.last_seen: float = time.time()
        self.token_rate: float = 0.0
        self.retry_count: int = 0
        self.last_verdict_ts: float = 0.0
        self.saw_change: bool = False
        self.last_git_sig: str | None = None
        self.last_query_sig: str | None = None
        self.last_change_ts: float | None = None
        self.unchanged_cycles: int = 0
        self.started_ts: float = time.time()


class Watcher:
    """Singleton watcher — one loop, one registry."""

    def __init__(self, poll_interval_s: int = 45):
        self.registry: dict[str, SessionState] = {}
        self.poll_interval_s = poll_interval_s
        self._loop_task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    # ------------------------------------------------------------------
    # Registry management
    # ------------------------------------------------------------------

    def register(self, session_id: str, **kwargs: Any) -> SessionState:
        st = SessionState(session_id, **kwargs)
        self.registry[session_id] = st
        logger.info("watcher registered session %s (pid=%s)", session_id, kwargs.get("pid"))
        return st

    def unregister(self, session_id: str) -> None:
        self.registry.pop(session_id, None)
        logger.info("watcher unregistered session %s", session_id)

    def get(self, session_id: str) -> SessionState | None:
        return self.registry.get(session_id)

    # ------------------------------------------------------------------
    # Event-driven update
    # ------------------------------------------------------------------

    def on_event(self, session_id: str, ev: dict[str, Any]) -> None:
        """Update last_seen/token_rate from an incoming event."""
        st = self.registry.get(session_id)
        if st is None:
            return
        st.last_seen = time.time()
        tokens = ev.get("tokens", {})
        if isinstance(tokens, dict):
            total = tokens.get("input", 0) + tokens.get("output", 0)
            if total > 0:
                st.token_rate = total / 60.0  # smoothed over 1 min

    # ------------------------------------------------------------------
    # Polling loop
    # ------------------------------------------------------------------

    async def loop(self) -> None:
        """Main singleton loop — runs forever until stopped."""
        logger.info("watcher loop started (interval=%ss)", self.poll_interval_s)
        while not self._stop_event.is_set():
            for sid, st in list(self.registry.items()):
                try:
                    self._check_session(sid, st)
                except Exception:
                    logger.exception("watcher check failed for %s", sid)
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self.poll_interval_s,
                )
                break  # stop event was set
            except asyncio.TimeoutError:
                continue  # normal poll interval

    def _check_session(self, session_id: str, st: SessionState) -> None:
        """Poll one session's signals and update verdict."""
        sig = self._poll_state(st)
        self._persist_signal(session_id, sig)
        v = verdict(vars(st), sig)
        st.last_verdict_ts = time.time()

        if v != st.status:
            logger.info("watcher %s: %s -> %s", session_id, st.status, v)
            st.status = v
            self._handle_verdict(session_id, st, v)

    def _handle_verdict(self, session_id: str, st: SessionState, v: str) -> None:
        """React to a verdict transition."""
        if v == "done":
            self._complete_and_advance(session_id, st)
            return
        from backend.watcher.controls import _on_verdict
        _on_verdict(session_id, st, v)

    def _poll_state(self, st: SessionState) -> dict[str, Any]:
        now = time.time()
        git_sig = _git_state_signature(st.worktree)
        fs_changed = git_sig != st.last_git_sig
        conv_ids = _resolve_node_conversation_ids(st.session_id, st.node_id)
        qsig = node_signal(AIONUI_DB, conv_ids)
        query_sig = qsig.get("latest_sig")
        query_changed = query_sig != st.last_query_sig
        if fs_changed:
            st.last_git_sig = git_sig

        if query_changed:
            st.last_query_sig = query_sig

        if fs_changed or query_changed:
            st.last_seen = now
            st.last_change_ts = now
            st.saw_change = True
            st.unchanged_cycles = 0
        else:
            st.unchanged_cycles += 1

        quiet_for = (now - st.last_change_ts) if st.last_change_ts else None
        stable_polls = st.unchanged_cycles >= 2
        terminal = bool(
            stable_polls and quiet_for is not None and quiet_for >= st.thresholds.get("settle_s", 30) and not fs_changed and not query_changed and (st.saw_change or qsig.get("have_data", False))
        )
        last_activity = (qsig.get("last_activity_ms", 0) / 1000.0) or st.last_change_ts or st.started_ts

        return {
            "pid_alive": _pid_alive(st.pid),
            "terminal": terminal,
            "quota_suspected": False,
            "token_rate": 1.0 if fs_changed else 0.0,
            "fs_changed": fs_changed,
            "last_activity": last_activity,
            "any_error": qsig.get("any_error", False),
            "error_codes": qsig.get("error_codes", []),
            "age_s": qsig.get("age_s"),
            "have_query_data": qsig.get("have_data", False),
            "query_changed": query_changed,
            "latest_query_sig": query_sig,
            "watcher_node_id": st.node_id,
            "conv_ids": conv_ids,
            "query_rows": qsig.get("rows", []),
        }

    def _persist_signal(self, session_id: str, sig: dict[str, Any]) -> None:
        try:
            with queries.conn() as c, c.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO session_signals
                    (session_id, ts, token_rate, last_activity, terminal, quota_suspected, pid_alive, fs_changed,
                     any_error, error_codes, age_s, watcher_node_id, signal_snapshot)
                    VALUES (%s, NOW(), %s, TO_TIMESTAMP(%s), %s, %s, %s, %s,
                            %s, %s::jsonb, %s, %s, %s::jsonb)
                    """,
                    (
                        session_id,
                        sig.get("token_rate", 0.0),
                        sig.get("last_activity", time.time()),
                        sig.get("terminal", False),
                        sig.get("quota_suspected", False),
                        sig.get("pid_alive", True),
                        sig.get("fs_changed", False),
                        sig.get("any_error", False),
                        json.dumps(sig.get("error_codes", [])),
                        sig.get("age_s"),
                        sig.get("watcher_node_id"),
                        json.dumps(sig),
                    ),
                )
        except Exception:
            logger.exception("failed to persist signal for %s", session_id)

    def _complete_and_advance(self, session_id: str, st: SessionState) -> None:
        if not st.node_id or not st.plan_id or not st.worktree:
            return

        # ── Evaluator gate (L1 deterministic → L2 rubric) ──────────────────
        decision_l2_score: float | None = None
        try:
            task_checks = _load_node_checks(st.plan_id, st.node_id)
            if task_checks:
                decision = evaluate_gate(task_checks, st.worktree, l2_fn=run_l2)
                decision_l2_score = decision.goal_review
                if decision.action == "remediate":
                    logger.info(
                        "Evaluator gate: remediate %s/%s (%s), inserting remediation node",
                        session_id, st.node_id, decision.reason.get("layer", "?"),
                    )
                    # Write goal_review before remediation
                    if decision.goal_review is not None:
                        _update_node_session_score(st.plan_id, st.node_id, decision.goal_review)
                    plan = _load_plan_by_id(st.plan_id)
                    if plan and plan.get("dag"):
                        dag = plan["dag"]
                        failed = next(
                            (n for n in dag if n.get("id") == st.node_id),
                            None,
                        )
                        if failed:
                            insert_remediation(
                                plan_id=st.plan_id,
                                failed_node=failed,
                                decision=decision.reason,
                                existing_chunks=dag,
                            )
                    return  # do NOT commit or advance
        except JudgeUnavailableError:
            # FAIL LOUD — never silently pass when judge is unavailable (Gate 01.6)
            logger.error(
                "JUDGE_UNAVAILABLE for %s/%s — node NOT committed. "
                "goal_review=NULL, judge_error recorded.",
                session_id, st.node_id,
            )
            _record_judge_error(st.plan_id, st.node_id)
            return  # do NOT commit — loud failure
        except Exception:
            logger.exception("evaluator gate failed for %s/%s — proceeding with commit", session_id, st.node_id)
            # Fail open: if evaluator itself errors (not judge-related), allow the node to commit

        # ── Atomic commit: node_session + tasks + runs, ONE transaction ────
        # (File 03.6: terminal-state atomicity — no lagging "running")
        try:
            tag = commit_node(st.worktree, st.node_id, summary=f"watcher complete {st.node_id}")
        except Exception:
            logger.exception("failed to commit for %s %s", session_id, st.node_id)
            return

        run_id = _resolve_active_run_id(st.plan_id)
        # Resolve the correct node_session ID + backend from existing record,
        # so that UPSERT hits ON CONFLICT (UPDATE) instead of failing NOT NULL.
        ns_id = st.node_session_id
        ns_backend = "opencode"
        if run_id and st.node_id:
            try:
                with queries.conn() as c2, c2.cursor() as cur2:
                    cur2.execute(
                        "SELECT id, backend FROM node_sessions WHERE run_id = %s AND node_id = %s",
                        (run_id, st.node_id),
                    )
                    row = cur2.fetchone()
                    if row:
                        ns_id = dict(row)["id"]
                        ns_backend = dict(row)["backend"]
            except Exception:
                pass
        if not ns_id:
            ns_id = f"{run_id or '?'}_{st.node_id}"

        l1_pass = True  # L1 passed if we reached here (L2 may have failed -> remediated above)
        with queries.conn() as c, c.cursor() as cur:
            # 1. UPSERT node_session with terminal state
            cur.execute(
                """INSERT INTO node_sessions
                   (id, run_id, node_id, backend, verdict, l1_pass, goal_review, commit_tag, finished_at)
                   VALUES (%s, %s, %s, %s, 'done', %s, %s, %s, NOW())
                   ON CONFLICT (id) DO UPDATE SET
                     verdict = 'done',
                     l1_pass = COALESCE(EXCLUDED.l1_pass, node_sessions.l1_pass),
                     goal_review = COALESCE(EXCLUDED.goal_review, node_sessions.goal_review),
                     commit_tag = EXCLUDED.commit_tag,
                     finished_at = NOW()
                """,
                (
                    ns_id,
                    run_id,
                    st.node_id,
                    ns_backend,
                    l1_pass,
                    decision_l2_score,
                    tag,
                ),
            )

            # 2. Mark tasks row done (backward compat — will be replaced by node_sessions in future)
            cur.execute(
                """UPDATE tasks
                      SET status = 'done', completion_signal = 'watcher_done',
                          node_commit_tag = %s, updated_at = NOW()
                    WHERE plan_id = %s AND node_id = %s
                """,
                (tag, st.plan_id, st.node_id),
            )

            # 3. Check if ALL nodes done via node_sessions (pre-created for all nodes at launch)
            cur.execute(
                """SELECT n.node_id, ns.verdict
                    FROM (SELECT jsonb_array_elements(dag::jsonb)->>'id' AS node_id FROM plans WHERE plan_id = %s) n
                    LEFT JOIN node_sessions ns ON ns.run_id = %s AND ns.node_id = n.node_id
                """,
                (st.plan_id, run_id),
            )
            rows = cur.fetchall()
            all_done = all(
                dict(r).get("verdict") == "done" for r in rows
            ) if rows else False

            cur.execute(
                """UPDATE runs
                      SET state = %s, finished_at = CASE WHEN %s THEN NOW() ELSE finished_at END
                    WHERE id = %s
                """,
                ("done" if all_done else "running", all_done, run_id),
            )
        # Transaction commits on context manager exit

        plan = _load_plan_by_id(st.plan_id)
        if not plan:
            return
        next_node = _next_ready_node(st.plan_id, plan)

        if not next_node:
            st.status = "done"
            # Write Langfuse score for the whole run when complete
            if decision_l2_score is not None and run_id:
                try:
                    from backend.observability.langfuse_client import get_langfuse
                    lf = get_langfuse()
                    lf.create_score(
                        name="run_complete",
                        value=round(decision_l2_score, 4),
                        data_type="NUMERIC",
                        trace_id=run_id,
                        comment=f"Run {run_id} completed. Final node {st.node_id} score={decision_l2_score}",
                    )
                    lf.flush()
                except Exception:
                    pass
            return

        try:
            # Set next node's verdict to running before spawning
            next_nid = next_node.get("id")
            with queries.conn() as c, c.cursor() as cur:
                cur.execute(
                    """UPDATE node_sessions SET verdict = 'running'
                       WHERE run_id = %s AND node_id = %s
                       RETURNING id""",
                    (run_id, next_nid),
                )
                row = cur.fetchone()
                if row:
                    st.node_session_id = dict(row)["id"]
                c.commit()

            dep_context = ""
            deps = next_node.get("depends_on", []) or []
            if deps:
                dep_context = build_node_context(st.worktree, deps)

            aionui = AionUiClient(os.environ.get("AIONUI_HOST", "http://127.0.0.1:40937"))
            wm = WorktreeManager(os.environ.get("WORKSPACE_ROOT", "/opt/aipc/conductor/workspace"))
            plan["worktree_path"] = st.worktree
            conv_map = spawn_node_team(
                node=next_node,
                plan=plan,
                session_id=session_id,
                aionui=aionui,
                wm=wm,
                members=next_node.get("members", [next_node.get("agent_config", "opencode:backend-executor")]),
                dep_context=dep_context,
                db_url=os.environ.get("DATABASE_URL", ""),
                workspace_root=os.environ.get("WORKSPACE_ROOT", "/opt/aipc/conductor/workspace"),
                auto_approve=plan.get("auto_approve", True),
            )
            orch_conv = conv_map.get("orchestrator") or next(iter(conv_map.values()), None)
            st.node_id = next_nid
            st.conversation_id = orch_conv
            st.started_ts = time.time()
            st.last_seen = st.started_ts
            st.last_change_ts = None
            st.saw_change = False
            st.last_git_sig = _git_state_signature(st.worktree)
            st.unchanged_cycles = 0
            st.status = VERDICT_RUNNING
        except Exception:
            logger.exception("failed to spawn next node for %s", session_id)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._loop_task is not None:
            logger.warning("watcher already started")
            return
        self._stop_event.clear()
        self._loop_task = asyncio.create_task(self.loop())

    async def stop(self) -> None:
        self._stop_event.set()
        if self._loop_task:
            await self._loop_task
            self._loop_task = None
        logger.info("watcher stopped")


# Module-level singleton
_watcher_singleton: Watcher | None = None


def get_watcher() -> Watcher:
    global _watcher_singleton
    if _watcher_singleton is None:
        _watcher_singleton = Watcher(poll_interval_s=45)
    return _watcher_singleton


def bootstrap_from_db() -> None:
    """Re-register active sessions after backend restart."""
    try:
        with queries.conn() as c, c.cursor() as cur:
            cur.execute(
                """
                SELECT s.session_id, s.project_id, s.worktree_path,
                       t.node_id,
                       l.aionui_conversation_id,
                       t.plan_id
                  FROM sessions s
                  JOIN tasks t ON t.session_id = s.session_id
                  LEFT JOIN aionui_links l ON l.task_id = t.task_id
                 WHERE s.worktree_path IS NOT NULL
                   AND t.status IN ('open', 'in_progress')
                 ORDER BY t.created_at ASC
                """
            )
            rows = [dict(r) for r in cur.fetchall()]
    except Exception:
        logger.exception("watcher bootstrap failed")
        return

    watcher = get_watcher()
    seen: set[str] = set()
    for row in rows:
        sid = row.get("session_id")
        if not sid or sid in seen:
            continue
        seen.add(sid)
        st = watcher.register(
            sid,
            pid=os.getpid(),
            worktree=row.get("worktree_path"),
            conversation_id=row.get("aionui_conversation_id"),
            node_id=row.get("node_id"),
            plan_id=row.get("plan_id"),
            project_id=row.get("project_id"),
        )
        st.last_git_sig = _git_state_signature(st.worktree)
        # Bootstrap-only: if worktree already has uncommitted changes, treat as
        # "change already seen" so the settle timer starts immediately.
        if st.worktree and _worktree_is_dirty(st.worktree):
            st.saw_change = True
            st.last_change_ts = time.time()


def _git_state_signature(worktree_path: str | None) -> str | None:
    if not worktree_path:
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


def _worktree_is_dirty(worktree_path: str | None) -> bool:
    if not worktree_path:
        return False
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=worktree_path,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return bool(result.stdout.strip())
    except Exception:
        return False


def _pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False


def _resolve_node_conversation_ids(session_id: str, node_id: str | None) -> list[str]:
    if not node_id:
        return []
    try:
        with queries.conn() as c, c.cursor() as cur:
            cur.execute(
                """
                SELECT l.aionui_conversation_id
                  FROM tasks t
                  JOIN aionui_links l ON l.task_id = t.task_id
                 WHERE t.session_id = %s AND t.node_id = %s
                 ORDER BY l.created_at ASC
                """,
                (session_id, node_id),
            )
            conv_ids: list[str] = []
            for row in cur.fetchall():
                data = dict(row)
                conv_id = data.get("aionui_conversation_id")
                if conv_id:
                    conv_ids.append(str(conv_id))
            return conv_ids
    except Exception:
        logger.exception("failed to resolve conv ids for %s %s", session_id, node_id)
        return []


def _load_node_checks(plan_id: str, node_id: str | None) -> list:
    """Load evaluation checks for a plan node from the tasks table."""
    if not node_id:
        return []
    try:
        with queries.conn() as c, c.cursor() as cur:
            cur.execute(
                "SELECT checks FROM tasks WHERE plan_id = %s AND node_id = %s",
                (plan_id, node_id),
            )
            row = cur.fetchone()
            if not row:
                return []
            raw = dict(row).get("checks")
            if not raw:
                return []
            import json
            if isinstance(raw, str):
                raw = json.loads(raw)
            return list(raw) if isinstance(raw, list) else []
    except Exception:
        logger.exception("failed to load checks for %s/%s", plan_id, node_id)
        return []


def _load_plan_by_id(plan_id: str) -> dict[str, Any] | None:
    """Load a plan by plan_id (v5.1: no session_id on plans)."""
    with queries.conn() as c, c.cursor() as cur:
        cur.execute(
            "SELECT plan_id, project_id, user_intent, goal, success, dag FROM plans WHERE plan_id = %s",
            (plan_id,),
        )
        row = cur.fetchone()
    if not row:
        return None
    plan = dict(row)
    dag = plan.get("dag") or []
    if isinstance(dag, str):
        try:
            dag = json.loads(dag)
        except json.JSONDecodeError:
            dag = []
    plan["dag"] = dag if isinstance(dag, list) else []
    success_raw = plan.get("success")
    if isinstance(success_raw, str):
        try:
            plan["success"] = json.loads(success_raw)
        except (json.JSONDecodeError, TypeError):
            plan["success"] = {"text": str(success_raw)}
    return plan


def _next_ready_node(plan_id: str, plan: dict[str, Any]) -> dict[str, Any] | None:
    """Find the next ready node whose deps are all done and who isn't already running."""
    dag = plan.get("dag", [])
    with queries.conn() as c, c.cursor() as cur:
        cur.execute(
            """SELECT node_id, verdict FROM node_sessions
                WHERE run_id IN (SELECT id FROM runs WHERE plan_id = %s)""",
            (plan_id,),
        )
        ns_rows = [dict(r) for r in cur.fetchall()]
    done = {r.get("node_id") for r in ns_rows if r.get("verdict") == "done"}
    running = {r.get("node_id") for r in ns_rows if r.get("verdict") == "running"}
    for node in dag:
        nid = node.get("id")
        if nid in done or nid in running:
            continue
        deps = node.get("depends_on", []) or []
        if all(dep in done for dep in deps):
            return node
    return None


def _resolve_active_run_id(plan_id: str) -> str | None:
    """Return the latest non-finished run id for a plan (or None)."""
    try:
        with queries.conn() as c, c.cursor() as cur:
            cur.execute(
                "SELECT id FROM runs WHERE plan_id = %s AND state NOT IN ('done','failed','cancelled') ORDER BY created_at DESC LIMIT 1",
                (plan_id,),
            )
            row = cur.fetchone()
            if row:
                return dict(row).get("id")
    except Exception:
        logger.exception("failed to resolve run_id for %s", plan_id)
    return None


def _update_node_session_score(plan_id: str, node_id: str, score: float) -> None:
    """Write L2 goal_review to node_sessions (OLTP path for ratchet)."""
    try:
        with queries.conn() as c, c.cursor() as cur:
            cur.execute(
                """UPDATE node_sessions
                      SET goal_review = %s
                     WHERE run_id IN (SELECT id FROM runs WHERE plan_id = %s)
                       AND node_id = %s
                       AND (goal_review IS NULL OR goal_review != %s)
                """,
                (score, plan_id, node_id, score),
            )
    except Exception:
        logger.exception("failed to write goal_review for %s/%s", plan_id, node_id)


def _record_judge_error(plan_id: str, node_id: str) -> None:
    """Record judge-unavailable error on the node session.

    Sets ``goal_review=NULL`` and writes a ``session_signals`` row
    with ``type='judge_error'`` so the UI can surface it.

    This is the spec-mandated "loud failure" path (Gate 01.6).
    The node is NOT committed — left in ``running`` for human review.
    """
    try:
        with queries.conn() as c, c.cursor() as cur:
            cur.execute(
                """UPDATE node_sessions
                      SET goal_review = NULL
                     WHERE run_id IN (SELECT id FROM runs WHERE plan_id = %s)
                       AND node_id = %s
                """,
                (plan_id, node_id),
            )
            run_id = _resolve_active_run_id(plan_id)
            if run_id:
                session_id = f"{run_id}_{node_id}"
                sig_id = f"judge_err_{session_id}"
                cur.execute(
                    """INSERT INTO session_signals
                       (id, session_id, name, value, type, metadata)
                       VALUES (%s, %s, 'judge_error', 1, 'judge_error',
                               jsonb_build_object('plan_id', %s, 'node_id', %s,
                                                  'message', 'All judge models unreachable, node NOT committed'))
                       ON CONFLICT (id) DO NOTHING
                    """,
                    (sig_id, session_id, plan_id, node_id),
                )
            c.commit()
        logger.error(
            "JUDGE_ERROR recorded for plan=%s node=%s — "
            "goal_review=NULL, node left in 'running' for human review",
            plan_id, node_id,
        )
    except Exception:
        logger.exception("failed to record judge_error for %s/%s", plan_id, node_id)
