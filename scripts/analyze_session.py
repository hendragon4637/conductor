"""
Conductor v4 — Langfuse Session→Node→Trace pipeline (Phase 1.5).

Maps one Conductor session → one Langfuse Session.
Each plan node → one Langfuse Trace within that session.
All node processes (AionUi messages, OpenCode tokens, git commits) →
  observations/spans/events nested in the node's trace.

Supports:
  - Per-node team conversations (lead + teammates) from AionUi SQLite
  - Cross-team node retries (e.g. node-3 original + retried in different team)
  - Orphaned conversations not in aionui_links (discovered via team name scan)
  - OpenCode token records mapped to nodes by time range

Usage:
  python3 /opt/aipc/conductor/.venv/bin/python3 \\
    /opt/aipc/conductor/scripts/analyze_session.py \\
    --conductor-session 0c99b7ce-16c5-42e6-8244-2df1dc5a40ca \\
    --session-label finance_run_v2

Output:
  /opt/aipc/conductor/analysis/<label>/  (directory with per-node .md reports)
  Langfuse trace visible at LANGFUSE_HOST
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
import opentelemetry.trace as otel_trace_api

# ---------------------------------------------------------------------------
# Load .env before reading env vars at module level
# ---------------------------------------------------------------------------
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# ---------------------------------------------------------------------------
# Paths & config
# ---------------------------------------------------------------------------
AIONUI_DB = os.environ.get(
    "AIONUI_DB",
    "/home/aipc/.config/AionUi/aionui/aionui-backend.db",
)
OPENCODE_DB = os.path.expanduser("~/.local/share/opencode/opencode.db")
LANGFUSE_HOST = os.environ.get("LANGFUSE_HOST", "http://127.0.0.1:3001")
LANGFUSE_PUBLIC_KEY = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
LANGFUSE_SECRET_KEY = os.environ.get("LANGFUSE_SECRET_KEY", "")
CONDUCTOR_DB_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://aipc:CHANGEME@127.0.0.1:5432/aipc_conductor?sslmode=disable",
)
ANALYSIS_DIR = Path("/opt/aipc/conductor/analysis")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ms_to_iso(ms: int) -> str:
    if not ms:
        return ""
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat(timespec="seconds")


def dt_to_ms(dt: datetime | None) -> int:
    if not dt:
        return 0
    return int(dt.timestamp() * 1000)


def compact_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except TypeError:
        return str(value)


def ms_to_ns(ms: int, offset: int = 0) -> int:
    return (ms * 1_000_000) + offset if ms else offset


def build_turns(messages: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    turns: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_has_boundary = False

    for msg in messages:
        is_boundary = msg.get("type") in {"text", "tips"}
        if is_boundary and current and current_has_boundary:
            turns.append(current)
            current = [msg]
            current_has_boundary = True
            continue

        current.append(msg)
        if is_boundary:
            current_has_boundary = True

    if current:
        turns.append(current)

    return turns


def turn_name(turn_index: int, turn_messages: list[dict[str, Any]]) -> str:
    first = turn_messages[0]
    start_step = first.get("_global_step", 0)
    end_step = turn_messages[-1].get("_global_step", 0)
    boundary = next(
        (msg for msg in turn_messages if msg.get("type") in {"text", "tips"}),
        first,
    )
    boundary_type = boundary.get("type", "event")
    return f"turn-{turn_index:03d}-step-{start_step:03d}-{end_step:03d}-{boundary_type}"


def emit_langfuse_event(
    parent_observation: Any,
    *,
    name: str,
    input: Any = None,
    output: Any = None,
    metadata: Any = None,
    start_time_ns: int,
    level: str | None = None,
    status_message: str | None = None,
) -> Any:
    from langfuse._client.span import LangfuseEvent

    with otel_trace_api.use_span(parent_observation._otel_span):
        otel_span = parent_observation._langfuse_client._otel_tracer.start_span(
            name=name,
            start_time=start_time_ns,
        )

    return LangfuseEvent(
        otel_span=otel_span,
        langfuse_client=parent_observation._langfuse_client,
        input=input,
        output=output,
        metadata=metadata,
        environment=parent_observation._environment,
        release=parent_observation._release,
        version=None,
        level=level,
        status_message=status_message,
    ).end(end_time=start_time_ns + 1)


def sqlite_conn(path: str, ro: bool = True) -> sqlite3.Connection:
    if not os.path.isfile(path):
        raise FileNotFoundError(f"SQLite DB not found: {path}")
    uri = f"file:{path}?mode={'ro' if ro else 'rw'}" if ro else path
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def parse_team_name(name: str) -> str | None:
    """Extract node ID from team name like '[project] plan — node node-1'."""
    m = re.search(r'node\s+(node-\d+)', name)
    if m:
        return m.group(1)
    return None


# ===================================================================
# Data types
# ===================================================================

class NodeInfo:
    """Everything known about one plan node."""
    def __init__(
        self,
        node_id: str,
        role: str,
        task: str,
        success: str,
        depends_on: list[str],
        task_id: str | None = None,
        task_status: str | None = None,
        task_created: int = 0,
        task_updated: int = 0,
        commit_tag: str | None = None,
        teams: list[dict] | None = None,   # [{team_id, name, convs: [{conv_id, name, role, model}]}]
    ):
        self.node_id = node_id
        self.role = role
        self.task = task
        self.success = success
        self.depends_on = depends_on
        self.task_id = task_id
        self.task_status = task_status
        self.task_created = task_created
        self.task_updated = task_updated
        self.commit_tag = commit_tag
        self.teams = teams or []

    @property
    def all_conv_ids(self) -> list[str]:
        ids: list[str] = []
        for t in self.teams:
            for c in t.get("convs", []):
                if c.get("conv_id"):
                    ids.append(c["conv_id"])
        return ids

    @property
    def all_team_ids(self) -> list[str]:
        return [t["team_id"] for t in self.teams if t.get("team_id")]


# ===================================================================
# SECTION 1: Query Conductor DB (PostgreSQL)
# ===================================================================

def query_conductor_session(session_uuid: str) -> dict[str, Any]:
    """Query Conductor PostgreSQL for session + plan DAG + tasks + aionui_links.
    
    Returns:
        {
            "session": {session_id, project_id, worktree_path, branch, ...},
            "plan": {plan_id, user_intent, dag: [NodeInfo]}
        }
    """
    import psycopg2

    conn = psycopg2.connect(CONDUCTOR_DB_URL)
    cur = conn.cursor()

    cur.execute(
        "SELECT session_id, project_id, status, worktree_path, branch, user_intent, created_at, updated_at "
        "FROM sessions WHERE session_id = %s",
        (session_uuid,),
    )
    row = cur.fetchone()
    if not row:
        conn.close()
        raise ValueError(f"Session not found: {session_uuid}")
    
    session_info = {
        "session_id": row[0],
        "project_id": row[1],
        "status": row[2],
        "worktree_path": row[3],
        "branch": row[4],
        "user_intent": row[5] or "(not stored in sessions table, see plan)",
        "created_at": dt_to_ms(row[6]),
        "updated_at": dt_to_ms(row[7]),
    }

    cur.execute(
        "SELECT plan_id, user_intent, dag, approval_status, created_at "
        "FROM plans WHERE session_id = %s ORDER BY created_at DESC LIMIT 1",
        (session_uuid,),
    )
    plan_row = cur.fetchone()
    if not plan_row:
        conn.close()
        raise ValueError(f"No plan found for session: {session_uuid}")

    plan_info = {
        "plan_id": plan_row[0],
        "user_intent": plan_row[1],
        "dag_raw": plan_row[2],
        "approval_status": plan_row[3],
        "created_at": dt_to_ms(plan_row[4]),
    }

    dag: list[dict] = plan_row[2]
    if isinstance(dag, str):
        dag = json.loads(dag)

    cur.execute(
        "SELECT task_id, node_id, status, node_commit_tag, "
        "       EXTRACT(EPOCH FROM created_at)::bigint * 1000 AS created_ms, "
        "       EXTRACT(EPOCH FROM updated_at)::bigint * 1000 AS updated_ms "
        "FROM tasks WHERE session_id = %s ORDER BY created_at",
        (session_uuid,),
    )
    task_rows: list[dict] = []
    for r in cur.fetchall():
        task_rows.append({
            "task_id": r[0],
            "node_id": r[1],
            "status": r[2],
            "commit_tag": r[3],
            "created_ms": r[4] or 0,
            "updated_ms": r[5] or 0,
        })

    task_ids = tuple(t["task_id"] for t in task_rows)
    links: list[dict] = []
    if task_ids:
        placeholders = ",".join("%s" for _ in task_ids)
        cur.execute(
            f"SELECT link_id, task_id, aionui_team_id, aionui_conversation_id, langfuse_trace_id, status "
            f"FROM aionui_links WHERE task_id IN ({placeholders})",
            task_ids,
        )
        for r in cur.fetchall():
            links.append({
                "link_id": r[0],
                "task_id": r[1],
                "team_id": r[2],
                "conv_id": r[3],
                "langfuse_trace_id": r[4],
                "status": r[5],
            })

    conn.close()

    task_by_node: dict[str, dict] = {t["node_id"]: t for t in task_rows}
    link_by_task: dict[str, dict] = {lk["task_id"]: lk for lk in links}
    nodes: list[NodeInfo] = []
    for node_dict in dag:
        nid = node_dict.get("id", "")
        task_info = task_by_node.get(nid, {})
        link_info = link_by_task.get(task_info.get("task_id", ""), {})

        node = NodeInfo(
            node_id=nid,
            role=node_dict.get("role", ""),
            task=node_dict.get("task", ""),
            success=node_dict.get("success", ""),
            depends_on=node_dict.get("depends_on", []),
            task_id=task_info.get("task_id"),
            task_status=task_info.get("status"),
            task_created=task_info.get("created_ms", 0),
            task_updated=task_info.get("updated_ms", 0),
            commit_tag=task_info.get("commit_tag"),
        )
        nodes.append(node)

    return {
        "session": session_info,
        "plan": plan_info,
        "nodes": nodes,
    }


# ===================================================================
# SECTION 2: Enrich nodes with AionUi teams & conversations
# ===================================================================

def enrich_nodes_from_aionui(nodes: list[NodeInfo], project: str, session_uuid: str) -> None:
    """Query AionUi SQLite to find ALL teams matching this session pattern.
    
    Discovers both linked (from aionui_links) and orphaned (from team name scan)
    conversations per node.
    """
    conn = sqlite_conn(AIONUI_DB)

    cur = conn.execute(
        "SELECT id, name, agents, created_at FROM teams WHERE name LIKE ? ORDER BY created_at",
        (f"[{project}]%",),
    )
    all_teams: list[dict] = [dict(r) for r in cur.fetchall()]

    cur = conn.execute("SELECT id, name, type, status, model, extra, created_at, updated_at FROM conversations ORDER BY created_at")
    all_convs: dict[str, dict] = {}
    for r in cur.fetchall():
        rdict = dict(r)
        all_convs[rdict["id"]] = rdict
    conn.close()

    teams_by_node: dict[str, list[dict]] = {}
    for t in all_teams:
        node_id = parse_team_name(t["name"])
        if node_id:
            if node_id not in teams_by_node:
                teams_by_node[node_id] = []
            agents_raw = t.get("agents")
            convs: list[dict] = []
            if agents_raw:
                if isinstance(agents_raw, str):
                    try:
                        agents_raw = json.loads(agents_raw)
                    except json.JSONDecodeError:
                        agents_raw = []
                if isinstance(agents_raw, list):
                    for agent in agents_raw:
                        cid = agent.get("conversation_id")
                        if cid and cid in all_convs:
                            cdata = all_convs[cid]
                            convs.append({
                                "conv_id": cid,
                                "name": agent.get("name", cdata.get("name", "")),
                                "role": agent.get("role", ""),
                                "model": agent.get("model", cdata.get("model", "")),
                                "status": cdata.get("status", ""),
                                "created_at": cdata.get("created_at", 0),
                                "updated_at": cdata.get("updated_at", 0),
                            })
            teams_by_node[node_id].append({
                "team_id": t["id"],
                "name": t["name"],
                "convs": convs,
                "created_at": t.get("created_at", 0),
            })

    for node in nodes:
        node.teams = teams_by_node.get(node.node_id, [])


# ===================================================================
# SECTION 3: Map OpenCode sessions to nodes by time
# ===================================================================

def get_opencode_sessions_by_time(
    worktree_path: str | None,
    nodes: list[NodeInfo],
) -> dict[str, list[dict]]:
    """Query OpenCode DB for sessions, then map them to nodes by time range.
    
    Returns {node_id: [session_dict, ...]}.
    """
    if not worktree_path or not os.path.isfile(OPENCODE_DB):
        print("[warn] OpenCode DB not accessible, skipping OpenCode data")
        return {}

    conn = sqlite_conn(OPENCODE_DB)

    cur = conn.execute(
        "SELECT * FROM session WHERE directory = ? ORDER BY time_created",
        (worktree_path,),
    )
    all_sessions = [dict(r) for r in cur.fetchall()]

    if not all_sessions:
        print(f"[warn] No OpenCode sessions found for worktree: {worktree_path}")
        conn.close()
        return {}

    print(f"[opencode] Found {len(all_sessions)} sessions for worktree")

    session_msgs: dict[str, list[dict]] = {}
    for sess in all_sessions:
        sid = sess["id"]
        cur2 = conn.execute(
            "SELECT data FROM message WHERE session_id = ? ORDER BY time_created",
            (sid,),
        )
        msgs: list[dict] = []
        for row in cur2.fetchall():
            raw = row[0]
            if not raw:
                continue
            try:
                obj = json.loads(raw) if isinstance(raw, str) else raw
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            tokens = obj.get("tokens")
            created = obj.get("time", {})
            if isinstance(created, dict):
                created_ms = created.get("created", 0)
            else:
                created_ms = 0
            msgs.append({
                "tokens": tokens if isinstance(tokens, dict) else None,
                "modelID": obj.get("modelID", ""),
                "providerID": obj.get("providerID", ""),
                "agent": obj.get("agent", ""),
                "role": obj.get("role", ""),
                "parentID": obj.get("parentID", ""),
                "time_created": obj.get("time", {}).get("created", 0) if isinstance(obj.get("time"), dict) else 0,
                "session_id": sid,
            })
        session_msgs[sid] = msgs
    conn.close()

    node_time_ranges: list[tuple[str, int, int]] = [
        (node.node_id, node.task_created, node.task_updated) for node in nodes
    ]
    node_time_ranges.sort(key=lambda x: x[1])

    result: dict[str, list[dict]] = {n.node_id: [] for n in nodes}

    for sess in all_sessions:
        sess_created = sess.get("time_created", 0)
        sess_updated = sess.get("time_updated", 0)

        best_node = None
        best_overlap = 0
        for nid, n_start, n_end in node_time_ranges:
            if n_start and n_end:
                overlap = max(0, min(sess_updated or sess_created, n_end) - max(sess_created, n_start))
                if overlap > best_overlap:
                    best_overlap = overlap
                    best_node = nid
            elif n_start and sess_created >= n_start:
                overlap = sess_created - n_start
                if overlap > best_overlap:
                    best_overlap = overlap
                    best_node = nid

        if best_node is None and node_time_ranges:
            first_start = node_time_ranges[0][1]
            if sess_created < first_start:
                best_node = "pre-node"

        node_id = best_node or "unmapped"
        session_data = dict(sess)
        session_data["messages"] = session_msgs.get(sess["id"], [])
        result.setdefault(node_id, []).append(session_data)

    total_mapped = sum(len(v) for v in result.values())
    print(f"[opencode] Mapped {total_mapped} sessions across {len([k for k, v in result.items() if v])} nodes")
    return result


# ===================================================================
# SECTION 4: Get git data per node
# ===================================================================

def get_git_data(worktree_path: str | None, nodes: list[NodeInfo]) -> dict[str, list[str]]:
    """Get git log and tag info, split by node tags.
    
    Returns {node_id: [commit_lines]}.
    """
    result: dict[str, list[str]] = {}
    git_path = os.path.join(worktree_path, ".git")
    if not worktree_path or not (os.path.isdir(git_path) or os.path.isfile(git_path)):
        return result

    try:
        r = subprocess.run(
            ["git", "log", "--oneline", "--all", "--decorate=short"],
            cwd=worktree_path, capture_output=True, text=True, timeout=30,
        )
        all_commits = [l.strip() for l in r.stdout.strip().split("\n") if l.strip()]
    except Exception as e:
        print(f"[warn] git log failed: {e}")
        return result

    for node in nodes:
        if node.commit_tag:
            tag = node.commit_tag
            node_commits = [c for c in all_commits if tag in c]
            result[node.node_id] = node_commits

    result["unmapped"] = [c for c in all_commits if not any(
        n.commit_tag and n.commit_tag in c for n in nodes if n.commit_tag
    )]

    return result


# ===================================================================
# SECTION 5: Langfuse trace builder — Session→Node→Observations
# ===================================================================

def ingest_to_langfuse(
    label: str,
    session_uuid: str,
    session_info: dict[str, Any],
    nodes: list[NodeInfo],
    opencode_sessions_by_node: dict[str, list[dict]],
    git_data_by_node: dict[str, list[str]],
    worktree_path: str | None,
):
    """Build one Langfuse Session with per-node Traces (SDK v4 OpenTelemetry)."""
    if not LANGFUSE_PUBLIC_KEY or not LANGFUSE_SECRET_KEY:
        print("[langfuse] SKIPPED — missing LANGFUSE_PUBLIC_KEY or LANGFUSE_SECRET_KEY")
        return

    from langfuse import get_client, propagate_attributes

    # Must configure env before first call to get_client()
    os.environ.setdefault("LANGFUSE_HOST", LANGFUSE_HOST)
    os.environ.setdefault("LANGFUSE_PUBLIC_KEY", LANGFUSE_PUBLIC_KEY)
    os.environ.setdefault("LANGFUSE_SECRET_KEY", LANGFUSE_SECRET_KEY)
    LANGFS = get_client()

    project = session_info.get("project_id", "unknown")
    session_label = f"{project}/{session_uuid[:8]}"
    session_start = session_info.get("created_at", int(time.time() * 1000))

    traces_created = 0

    for node in nodes:
        trace_name = f"{label}-{node.node_id}"

        all_conv_ids = node.all_conv_ids
        aionui_messages: list[dict] = []
        aionui_teams_used: list[str] = []
        conversation_order: list[tuple[str, str]] = []

        for team in node.teams:
            aionui_teams_used.append(team["team_id"])
            for conv in team.get("convs", []):
                cid = conv["conv_id"]
                conversation_order.append((cid, conv.get("name", cid)))
                try:
                    conn = sqlite_conn(AIONUI_DB)
                    cur = conn.execute(
                        "SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at ASC",
                        (cid,),
                    )
                    for row in cur.fetchall():
                        r = dict(row)
                        content = r.get("content", "{}")
                        if isinstance(content, str):
                            try:
                                r["content"] = json.loads(content)
                            except json.JSONDecodeError:
                                r["content"] = {"content": content}
                        r["_conv_id"] = cid
                        r["_conv_name"] = conv.get("name", "")
                        aionui_messages.append(r)
                    conn.close()
                except Exception as e:
                    print(f"  [warn] Could not load AionUi messages for conv {cid}: {e}")

        conv_tag = node.commit_tag or "no-commit-tag"
        team_names = [t["name"] for t in node.teams]

        node_start_ms = node.task_created
        node_end_ms = node.task_updated
        if not node_start_ms and aionui_messages:
            node_start_ms = aionui_messages[0].get("created_at", 0)
            node_end_ms = aionui_messages[-1].get("created_at", 0)

        aionui_messages.sort(
            key=lambda msg: (
                msg.get("created_at", 0),
                msg.get("_conv_id", ""),
                msg.get("id", ""),
            )
        )
        for step, msg in enumerate(aionui_messages, start=1):
            msg["_global_step"] = step

        messages_by_conv: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for msg in aionui_messages:
            messages_by_conv[msg.get("_conv_id", "")].append(msg)

        for conv_messages in messages_by_conv.values():
            for conv_step, msg in enumerate(conv_messages, start=1):
                msg["_conv_step"] = conv_step

        total_token_gens = 0

        with LANGFS.start_as_current_observation(
            as_type="span",
            name=trace_name,
            input={
                "node": node.node_id,
                "role": node.role,
                "task": node.task,
                "teams": team_names,
                "conversations": all_conv_ids,
                "task_status": node.task_status,
                "commit_tag": conv_tag,
            },
            end_on_exit=False,
        ) as root:
            root._otel_span._start_time = ms_to_ns(node_start_ms or session_start)
            with propagate_attributes(
                session_id=session_label,
                trace_name=trace_name,
                tags=["analysis", "phase1.5", label, node.node_id],
                metadata={
                    "session_uuid": session_uuid,
                    "node_id": node.node_id,
                    "task_id": node.task_id or "",
                    "teams": json.dumps(aionui_teams_used),
                    "conv_ids": json.dumps(all_conv_ids),
                    "worktree": worktree_path or "",
                },
            ):
                with LANGFS.start_as_current_observation(
                    as_type="span",
                    name="aionui",
                    input={"conversation_count": len(conversation_order), "message_count": len(aionui_messages)},
                    end_on_exit=False,
                ) as aionui_span:
                    aionui_span._otel_span._start_time = ms_to_ns(node_start_ms or session_start)
                    for conv_id, conv_name in conversation_order:
                        conv_messages = messages_by_conv.get(conv_id, [])
                        if not conv_messages:
                            continue

                        conv_span_name = f"{conv_name} · {conv_id}"
                        with LANGFS.start_as_current_observation(
                            as_type="span",
                            name=conv_span_name,
                            input={
                                "conversation_id": conv_id,
                                "conversation_name": conv_name,
                                "message_count": len(conv_messages),
                            },
                            end_on_exit=False,
                        ) as conv_span:
                            conv_span._otel_span._start_time = ms_to_ns(conv_messages[0].get("created_at", 0))
                            for turn_index, turn_messages in enumerate(build_turns(conv_messages), start=1):
                                with LANGFS.start_as_current_observation(
                                    as_type="span",
                                    name=turn_name(turn_index, turn_messages),
                                    input={
                                        "conversation_id": conv_id,
                                        "conversation_name": conv_name,
                                        "message_ids": [msg.get("id", "") for msg in turn_messages],
                                        "step_start": turn_messages[0].get("_global_step", 0),
                                        "step_end": turn_messages[-1].get("_global_step", 0),
                                        "conv_step_start": turn_messages[0].get("_conv_step", 0),
                                        "conv_step_end": turn_messages[-1].get("_conv_step", 0),
                                    },
                                    end_on_exit=False,
                                ) as turn_span:
                                    turn_span._otel_span._start_time = ms_to_ns(turn_messages[0].get("created_at", 0))
                                    for msg in turn_messages:
                                        mtype = msg.get("type", "unknown")
                                        raw_content = msg.get("content", {}) or {}
                                        created_ms = msg.get("created_at", 0)
                                        msg_status = msg.get("status", "")
                                        span_name = f"step-{msg.get('_global_step', 0):03d}-{mtype}"
                                        common_metadata = {
                                            "msg_id": msg.get("id", ""),
                                            "db_msg_id": msg.get("msg_id", "") or "",
                                            "conv_id": conv_id,
                                            "conv_name": conv_name,
                                            "position": msg.get("position", ""),
                                            "status": msg_status,
                                            "ts_ms": created_ms,
                                            "global_step": msg.get("_global_step", 0),
                                            "conv_step": msg.get("_conv_step", 0),
                                            "payload_type": raw_content.get("type", "") if isinstance(raw_content, dict) else "",
                                            "payload_json": compact_json(raw_content),
                                        }
                                        event_time_ns = ms_to_ns(created_ms, msg.get("_global_step", 0))

                                        if mtype == "thinking":
                                            obs = LANGFS.start_observation(
                                                name=span_name,
                                                as_type="generation",
                                                input={
                                                    "type": mtype,
                                                    "thought": raw_content.get("content", ""),
                                                },
                                                metadata={
                                                    **common_metadata,
                                                    "duration_ms": raw_content.get("duration_ms", 0),
                                                },
                                            )
                                            obs._otel_span._start_time = event_time_ns
                                            obs.end(end_time=event_time_ns + 1)
                                        elif mtype == "acp_tool_call":
                                            update = raw_content.get("update", {}) if isinstance(raw_content, dict) else {}
                                            obs = LANGFS.start_observation(
                                                name=span_name,
                                                as_type="span",
                                                input={
                                                    "type": mtype,
                                                    "kind": update.get("kind", "tool_call"),
                                                    "raw_input": update.get("raw_input"),
                                                    "session_update": update.get("session_update"),
                                                    "tool_call_id": update.get("tool_call_id"),
                                                    "tool_title": update.get("title", ""),
                                                },
                                                metadata={
                                                    **common_metadata,
                                                    "session_id": raw_content.get("session_id", "") if isinstance(raw_content, dict) else "",
                                                },
                                            )
                                            obs.update(
                                                output={
                                                    "raw_output": update.get("raw_output"),
                                                    "status": update.get("status"),
                                                    "content": update.get("content"),
                                                }
                                            )
                                            obs._otel_span._start_time = event_time_ns
                                            obs.end(end_time=event_time_ns + 1)
                                        else:
                                            event_input = {
                                                "type": mtype,
                                                "position": msg.get("position", ""),
                                                "status": msg_status,
                                            }
                                            event_output: dict[str, Any] = {}
                                            if isinstance(raw_content, dict):
                                                if "content" in raw_content:
                                                    if msg.get("position") == "right":
                                                        event_input["text"] = raw_content.get("content")
                                                    else:
                                                        event_output["text"] = raw_content.get("content")
                                                if "error" in raw_content:
                                                    event_output["error"] = raw_content.get("error")
                                            else:
                                                if msg.get("position") == "right":
                                                    event_input["content"] = raw_content
                                                else:
                                                    event_output["content"] = raw_content

                                            if not event_output:
                                                event_output = {"content": raw_content}

                                            emit_langfuse_event(
                                                turn_span,
                                                name=span_name,
                                                input=event_input,
                                                output=event_output,
                                                metadata=common_metadata,
                                                start_time_ns=event_time_ns,
                                                level="ERROR" if msg_status == "error" else None,
                                                status_message=raw_content.get("error", {}).get("message") if isinstance(raw_content, dict) else None,
                                            )

                                    turn_span.end(
                                        end_time=ms_to_ns(
                                            turn_messages[-1].get("created_at", 0),
                                            turn_messages[-1].get("_global_step", 0) + 1,
                                        )
                                    )

                            conv_span.end(
                                end_time=ms_to_ns(
                                    conv_messages[-1].get("created_at", 0),
                                    conv_messages[-1].get("_global_step", 0) + 1,
                                )
                            )

                    aionui_span.end(
                        end_time=ms_to_ns(
                            aionui_messages[-1].get("created_at", 0) if aionui_messages else (node_end_ms or node_start_ms or session_start),
                            len(aionui_messages) + 1,
                        )
                    )

                with LANGFS.start_as_current_observation(
                    as_type="span",
                    name="git",
                    input={"commit_count": len(git_data_by_node.get(node.node_id, []))},
                    end_on_exit=False,
                ) as git_span:
                    git_commits = git_data_by_node.get(node.node_id, [])
                    git_start_ms = node_end_ms or node_start_ms or session_start
                    git_span._otel_span._start_time = ms_to_ns(git_start_ms)
                    for gidx, line in enumerate(git_commits[:20]):
                        event_time_ns = ms_to_ns(git_start_ms, gidx)
                        emit_langfuse_event(
                            git_span,
                            name=f"git-{node.node_id}-{gidx:03d}",
                            output={"commit": line},
                            metadata={"source": "git", "node": node.node_id},
                            start_time_ns=event_time_ns,
                        )

                    git_span.end(end_time=ms_to_ns(git_start_ms, len(git_commits) + 1))

                root.end(end_time=ms_to_ns(node_end_ms or node_start_ms or session_start, len(aionui_messages) + total_token_gens + len(git_commits) + 1))

            traces_created += 1
            print(f"  [trace] Created '{trace_name}' with {len(aionui_messages)} msgs, "
                  f"{total_token_gens} token gens, {len(git_commits)} git events")

    LANGFS.flush()
    print(f"\n[langfuse] Session '{session_label}' with {traces_created} traces sent. Check {LANGFUSE_HOST}")


# ===================================================================
# SECTION 6: Markdown reporter (per-node)
# ===================================================================

def write_node_report(node: NodeInfo, output_dir: Path) -> str:
    """Write a markdown report for one node."""
    lines: list[str] = []
    lines.append(f"# Node Report: {node.node_id}\n")
    lines.append(f"Generated: {iso_now()}\n")
    lines.append("---\n")
    lines.append(f"- **Role:** {node.role}")
    lines.append(f"- **Task:** {node.task}")
    lines.append(f"- **Success Criterion:** {node.success}")
    lines.append(f"- **Depends On:** {', '.join(node.depends_on) if node.depends_on else '(none)'}")
    lines.append(f"- **Task ID:** {node.task_id or '(none)'}")
    lines.append(f"- **Task Status:** {node.task_status or '(none)'}")
    lines.append(f"- **Commit Tag:** {node.commit_tag or '(none)'}")
    lines.append(f"- **Created:** {ms_to_iso(node.task_created) if node.task_created else '(none)'}")
    lines.append(f"- **Updated:** {ms_to_iso(node.task_updated) if node.task_updated else '(none)'}")
    lines.append("")

    lines.append("## Teams & Conversations\n")
    if not node.teams:
        lines.append("*(No teams found)*\n")
    else:
        for team in node.teams:
            lines.append(f"### Team: {team['name']}")
            lines.append(f"- **Team ID:** `{team['team_id']}`")
            lines.append(f"- **Created:** {ms_to_iso(team.get('created_at', 0))}")
            lines.append("")
            convs = team.get("convs", [])
            if convs:
                lines.append("| Conv ID | Name | Role | Model | Status |")
                lines.append("|---------|------|------|-------|--------|")
                for c in convs:
                    lines.append(f"| {c.get('conv_id', '')} | {c.get('name', '')} | {c.get('role', '')} | {c.get('model', '')} | {c.get('status', '')} |")
                lines.append("")
            else:
                lines.append("*(No conversations in this team)*\n")

    return "\n".join(lines)


# ===================================================================
# Main
# ===================================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Langfuse Session→Node→Trace Pipeline"
    )
    parser.add_argument(
        "--conductor-session", required=True,
        help="Conductor session UUID (e.g. 0c99b7ce-16c5-42e6-8244-2df1dc5a40ca)",
    )
    parser.add_argument(
        "--session-label", default=None,
        help="Label for Langfuse trace (defaults to session short ID)",
    )
    parser.add_argument(
        "--skip-langfuse", action="store_true",
        help="Skip Langfuse ingestion (reports only)",
    )
    args = parser.parse_args()

    session_uuid = args.conductor_session
    label = args.session_label or f"cs-{session_uuid[:8]}"
    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)

    print(f"=== Langfuse Session→Node Pipeline ===")
    print(f"Session: {session_uuid}")
    print(f"Label:   {label}")
    print()

    # ---- Step 1: Query Conductor DB ----
    print("[1/5] Querying Conductor DB...")
    try:
        data = query_conductor_session(session_uuid)
    except ValueError as e:
        print(f"[FATAL] {e}")
        sys.exit(1)
    except ImportError:
        print("[FATAL] psycopg2 not available. Run: uv pip install psycopg2-binary")
        sys.exit(1)

    session_info = data["session"]
    plan_info = data["plan"]
    nodes = data["nodes"]
    worktree_path = session_info.get("worktree_path")

    print(f"  Session: {session_info['session_id']}")
    print(f"  Project: {session_info['project_id']}")
    print(f"  Worktree: {worktree_path}")
    print(f"  Plan: {plan_info['plan_id']} ({plan_info['approval_status']})")
    print(f"  Nodes: {[n.node_id for n in nodes]}")
    print()

    # ---- Step 2: Enrich with AionUi teams/conversations ----
    print("[2/5] Enriching nodes with AionUi data...")
    project = session_info.get("project_id", "finance-tracker")
    enrich_nodes_from_aionui(nodes, project, session_uuid)

    for node in nodes:
        team_count = len(node.teams)
        conv_count = len(node.all_conv_ids)
        print(f"  {node.node_id}: {team_count} team(s), {conv_count} conversation(s): {node.all_conv_ids}")

    # Detect node-3 retry: if node-3 has 2+ teams, it was retried
    for node in nodes:
        if len(node.teams) > 1:
            print(f"  ⚠  {node.node_id} has {len(node.teams)} teams — retry detected!")
            for i, t in enumerate(node.teams):
                print(f"      Team {i+1}: {t['name']} ({t['team_id']}) — "
                      f"{len(t.get('convs', []))} conversations")
    print()

    # ---- Step 3: Map OpenCode sessions to nodes ----
    print("[3/5] Mapping OpenCode sessions to nodes...")
    opencode_by_node = get_opencode_sessions_by_time(worktree_path, nodes)

    for node in nodes:
        count = len(opencode_by_node.get(node.node_id, []))
        print(f"  {node.node_id}: {count} OpenCode session(s)")

    # Show unmapped
    unmapped = opencode_by_node.get("unmapped", []) + opencode_by_node.get("pre-node", [])
    if unmapped:
        print(f"  (unmapped: {len(unmapped)} session(s))")
    print()

    # ---- Step 4: Get git data ----
    print("[4/5] Getting git data per node...")
    git_by_node = get_git_data(worktree_path, nodes)
    for node in nodes:
        count = len(git_by_node.get(node.node_id, []))
        print(f"  {node.node_id}: {count} git commit(s) tagged '{node.commit_tag}'")
    print()

    # ---- Write reports ----
    output_dir = ANALYSIS_DIR / label
    output_dir.mkdir(parents=True, exist_ok=True)
    for node in nodes:
        report = write_node_report(node, output_dir)
        path = output_dir / f"{node.node_id}.md"
        path.write_text(report)
        print(f"  [report] {path}")

    # ---- Step 5: Langfuse ingestion ----
    if args.skip_langfuse:
        print("\n[5/5] SKIPPED Langfuse ingestion (--skip-langfuse)")
    else:
        print("\n[5/5] Building Langfuse Session→Node traces...")
        ingest_to_langfuse(
            label=label,
            session_uuid=session_uuid,
            session_info=session_info,
            nodes=nodes,
            opencode_sessions_by_node=opencode_by_node,
            git_data_by_node=git_by_node,
            worktree_path=worktree_path,
        )

    print()
    print("=== Complete ===")
    print(f"Reports: {output_dir}/")
    print(f"Langfuse: {LANGFUSE_HOST}")


if __name__ == "__main__":
    main()
