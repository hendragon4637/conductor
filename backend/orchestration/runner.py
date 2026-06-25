from __future__ import annotations

import asyncio
import hashlib
import os
import subprocess
from typing import Any

from backend.aionui import AionUiClient, AionUiReader
from backend.backends.registry import is_self_orchestrating
from backend.builtins.git_ops import commit_node
from backend.builtins.handoff import build_node_context
from backend.db import queries
from backend.hermes_adapter import HermesClient
from backend.observability.ingest import ingest_run
from backend.orchestration.spawn import spawn_node_team
from backend.planning.store import save_node_session, update_run_state
from backend.watcher.supervisor import get_watcher
from backend.watcher.gitops import regression_gate
from backend.worktree import WorktreeManager


ORCHESTRATOR_MODEL_PRIMARY = os.environ.get(
    "ORCHESTRATOR_MODEL_PRIMARY",
    "deepseek-v4-flash",
)


def launch_run(
    run_id: str,
    run_row: dict[str, Any],
    plan_data: dict[str, Any],
    db_url: str | None = None,
    aionui_host: str | None = None,
    workspace_root: str | None = None,
) -> str:
    """Spawn the first ready node for a run; watcher owns subsequent advancement.

    Creates a node_session record and spawns the node team.
    Returns the session_id so the frontend can navigate to the Sessions tab.
    """
    _db_url = db_url or os.environ.get("DATABASE_URL", "")
    _aionui_host = aionui_host or os.environ.get("AIONUI_HOST", "http://127.0.0.1:40937")
    _wsr = workspace_root or os.environ.get("WORKSPACE_ROOT", "/opt/aipc/conductor/workspace")

    aionui = AionUiClient(_aionui_host)
    wm = WorktreeManager(_wsr)
    nodes = plan_data.get("dag", plan_data.get("nodes", []))
    first = next((n for n in nodes if not (n.get("depends_on", []) or [])), None)
    if not first:
        raise RuntimeError("No root node available to launch")

    import uuid
    session_id = str(uuid.uuid4())

    # Ensure prerequisite DB rows exist before spawning
    project_id = plan_data.get("project_id", "default")
    _ensure_project_and_session(_db_url, project_id, session_id, plan_data.get("description", ""))

    node_id = first.get("id") or first.get("node_id")
    backend_key = first.get("backend")
    if not backend_key:
        raise ValueError(f"Node {node_id} has no backend — decompose must set it")
    is_self_orch = is_self_orchestrating(backend_key)
    if is_self_orch:
        print(f"  [launch] Node {node_id} uses self-orchestrating backend {backend_key!r}")

    # Pre-create node_sessions for ALL nodes upfront so _complete_and_advance
    # can find each node's session by run_id + node_id during advancement.
    first_id = first.get("id") or first.get("node_id")
    ns_map: dict[str, str] = {}
    for node in nodes:
        nid = node.get("id") or node.get("node_id")
        if not nid:
            continue
        is_first = nid == first_id
        new_ns_id = f"ns_{uuid.uuid4().hex[:8]}"
        node_backend = node.get("backend", "opencode")
        node_members = node.get("members", [node.get("agent_config", "opencode:backend-executor")])
        save_node_session({
            "id": new_ns_id,
            "run_id": run_id,
            "node_id": nid,
            "backend": node_backend,
            "members": node_members,
            "verdict": "running" if is_first else "pending",
            "attempt": 1,
        })
        ns_map[nid] = new_ns_id

    ns_id = ns_map[first_id]
    members_raw = first.get("members", [first.get("agent_config", "opencode:backend-executor")])

    conv_map = spawn_node_team(
        node=first,
        plan=plan_data,
        session_id=session_id,
        aionui=aionui,
        wm=wm,
        members=members_raw,
        dep_context="",
        db_url=_db_url,
        workspace_root=_wsr,
        auto_approve=plan_data.get("auto_approve", True),
    )
    orch_conv_id = _find_orch_conv(conv_map)
    team_id = conv_map.get("__team_id__")
    worktree_path = plan_data.get("worktree_path")
    _update_session_runtime(session_id, worktree_path)

    first_members = first.get("members", [first.get("agent_config", "opencode:backend-executor")])
    save_node_session({
        "id": ns_id,
        "run_id": run_id,
        "node_id": first_id,
        "backend": first.get("backend", "opencode"),
        "members": first_members,
        "verdict": "running",
        "worktree": worktree_path,
        "attempt": 1,
        "aionui_conversation_id": orch_conv_id,
        "aionui_team_id": team_id,
    })
    for node in nodes:
        nid = node.get("id") or node.get("node_id")
        if nid == first_id or nid not in ns_map:
            continue
        save_node_session({
            "id": ns_map[nid],
            "run_id": run_id,
            "node_id": nid,
            "backend": node.get("backend", "opencode"),
            "members": node.get("members", [node.get("agent_config", "opencode:backend-executor")]),
            "verdict": "pending",
            "worktree": worktree_path,
            "attempt": 1,
        })

    watcher = get_watcher()
    st = watcher.register(
        session_id,
        pid=os.getpid(),
        worktree=worktree_path,
        conversation_id=orch_conv_id,
        node_id=node_id,
        plan_id=plan_data.get("plan_id"),
        project_id=plan_data.get("project_id"),
        node_session_id=ns_id,
    )
    st.last_git_sig = _git_state_signature(worktree_path)
    return session_id


async def run_plan(
    plan: dict[str, Any],
    session_id: str,
    db_url: str | None = None,
    aionui_host: str | None = None,
    aionui_db: str | None = None,
    workspace_root: str | None = None,
    run_id: str | None = None,
) -> dict[str, str]:
    """Execute an approved plan DAG.

    Each node executes as its own AionUi team (orchestrator + node members)
    in dependency order. After each node completes, Conductor commits and tags.
    Results dict maps node id to Langfuse trace id.

    Args:
        plan: Plan dict from DB (must have ``dag`` as list of nodes,
            ``plan_id``, ``user_intent``).
        session_id: DB session identifier.
    """
    _db_url = db_url or os.environ.get("DATABASE_URL", "")
    _aionui_host = aionui_host or os.environ.get("AIONUI_HOST", "http://127.0.0.1:40937")
    _aionui_db = aionui_db or os.environ.get(
        "AIONUI_DB",
        "/home/aipc/.config/AionUi/aionui/aionui-backend.db",
    )
    _wsr = workspace_root or os.environ.get("WORKSPACE_ROOT", "/opt/aipc/conductor/workspace")

    aionui = AionUiClient(_aionui_host)
    reader = AionUiReader(_aionui_db)
    wm = WorktreeManager(_wsr)

    nodes = plan.get("dag", plan.get("nodes", []))
    plan_id = plan["plan_id"]
    worktree_path = plan.get("worktree_path")

    done: set[str] = set()
    results: dict[str, str] = {}
    node_tags: dict[str, str] = {}

    while len(done) < len(nodes):
        ready = [
            n for n in nodes
            if n["id"] not in done
            and all(d in done for d in n.get("depends_on", []))
        ]

        if not ready and len(done) < len(nodes):
            raise RuntimeError(
                f"Deadlock: no ready nodes but {len(nodes) - len(done)} remain"
            )

        for node in ready:
            # Build dependency handoff context for this node
            dep_ids = node.get("depends_on", [])
            dep_context = ""
            if dep_ids and worktree_path:
                dep_context = build_node_context(
                    worktree=worktree_path,
                    dep_ids=dep_ids,
                )

            # Detect backend class for this node
            backend_key = node.get("backend", "opencode")
            is_self_orch = is_self_orchestrating(backend_key)

            # Spawn the node (class-a skips orchestrator via short-circuit)
            node_members = node.get("members", [node.get("agent_config", "opencode:backend-executor")])
            conv_map = spawn_node_team(
                node=node,
                plan=plan,
                session_id=session_id,
                aionui=aionui,
                wm=wm,
                members=node_members,
                dep_context=dep_context,
                db_url=_db_url,
                workspace_root=_wsr,
                auto_approve=plan.get("auto_approve", True),
            )

            # The first spawn creates the shared worktree path in-place on the plan.
            worktree_path = plan.get("worktree_path") or worktree_path
            _update_session_runtime(session_id, worktree_path)

            if is_self_orch:
                # Class-a: no orchestrator; monitor the self-orchestrating tool
                member_conv_id = next(iter(conv_map.values())) if conv_map else None
                if not member_conv_id:
                    raise RuntimeError(
                        f"No conversation for self-orchestrating node {node['id']}"
                    )

                if backend_key == "hermes":
                    run_id = conv_map.get("__run_id__", "")
                    if not run_id:
                        raise RuntimeError(
                            f"No Hermes run_id for node {node['id']}"
                        )
                    hermes = HermesClient()
                    final_status = await _wait_for_hermes_run(
                        run_id,
                        hermes,
                        worktree_path=worktree_path,
                    )
                else:
                    # opencode_omo: wait on the member AionUi conversation directly
                    final_status = await _wait_for_conversation(
                        aionui,
                        reader,
                        member_conv_id,
                        worktree_path=worktree_path,
                    )

                orch_conv_id = member_conv_id
            else:
                # Class-b: existing orchestrator behavior
                orch_conv_id = _find_orch_conv(conv_map)
                if not orch_conv_id:
                    raise RuntimeError(
                        f"No orchestrator conversation for node {node['id']}"
                    )

                final_status = await _wait_for_conversation(
                    aionui,
                    reader,
                    orch_conv_id,
                    worktree_path=worktree_path,
                )
            if final_status != "finished":
                raise RuntimeError(
                    f"Node {node['id']} did not finish successfully (status={final_status})"
                )

            if worktree_path and not regression_gate(worktree_path):
                raise RuntimeError(
                    f"Regression gate failed for node {node['id']} in {worktree_path}"
                )

            # Ingest into Langfuse
            trace_id = ingest_run(
                task_id=node.get("id", "unknown"),
                plan_id=plan_id,
                agent_config="orchestrator",
                engine="opencode",
                model=ORCHESTRATOR_MODEL_PRIMARY,
                conversation_id=orch_conv_id,
                reader=reader,
            )

            results[node["id"]] = trace_id

            # Commit and tag the node if we have a worktree path
            if worktree_path:
                tag = commit_node(
                    worktree=worktree_path,
                    node_id=node["id"],
                    summary=node.get("task", node.get("title", node.get("description", ""))),
                )
                node_tags[node["id"]] = tag
                _mark_node_done(session_id, node["id"], tag)
            else:
                _mark_node_done(session_id, node["id"], None)

            done.add(node["id"])

    if run_id:
        update_run_state(run_id, "done")
    reader.close()
    return results


def _find_orch_conv(conv_map: dict[str, str]) -> str | None:
    """Find the orchestrator conversation ID from the spawn map.

    The orchestrator is stored under the key 'orchestrator' or the
    first entry if no explicit orchestrator key exists.
    Skips internal keys prefixed with ``__`` (e.g. ``__team_id__``).
    """
    if "orchestrator" in conv_map:
        return conv_map["orchestrator"]
    for key in conv_map:
        if key.startswith("__"):
            continue
        return conv_map[key]
    return None


async def _wait_for_conversation(
    aionui: AionUiClient,
    reader: AionUiReader,
    conv_id: str,
    worktree_path: str | None = None,
    poll_interval: float = 3.0,
    timeout: float = 28800.0,
    settle_interval: float = 30.0,
    stall_timeout: float = 180.0,
) -> str:
    """Poll until the conversation reaches a terminal state.

    Temporary deterministic rule:
    - observe at least one git-visible change in the node worktree
    - if git state stops changing for ``settle_interval`` seconds => finished
    - if no git-visible change happens for ``stall_timeout`` seconds => stalled

    This intentionally avoids treating ordinary assistant output as completion.
    """
    elapsed = 0.0
    saw_change = False
    last_sig = _git_state_signature(worktree_path) if worktree_path else None
    last_change_at = 0.0
    while elapsed < timeout:
        conv = aionui.get_conversation(conv_id)
        status = conv.get("status")
        if status == "error":
            return status

        if worktree_path:
            current_sig = _git_state_signature(worktree_path)
            if current_sig != last_sig:
                saw_change = True
                last_change_at = elapsed
                last_sig = current_sig
            elif saw_change and (elapsed - last_change_at) >= settle_interval:
                return "finished"
            elif not saw_change and elapsed >= stall_timeout:
                return "stalled"
        elif status == "finished":
            return status

        await asyncio.sleep(poll_interval)
        elapsed += poll_interval

    return "timeout"


async def _wait_for_hermes_run(
    run_id: str,
    hermes: HermesClient,
    worktree_path: str | None = None,
    poll_interval: float = 3.0,
    timeout: float = 28800.0,
    settle_interval: float = 30.0,
    stall_timeout: float = 180.0,
) -> str:
    """Poll Hermes HTTP API until the run reaches a terminal state.

    Uses the same git-state settling heuristic as
    ``_wait_for_conversation``: observes git diff stability rather than
    relying on the Hermes-reported status alone.

    Returns:
        One of ``"finished"``, ``"stalled"``, ``"timeout"``, or the
        Hermes-reported status if it indicates failure.
    """
    elapsed = 0.0
    saw_change = False
    last_sig = _git_state_signature(worktree_path) if worktree_path else None
    last_change_at = 0.0
    while elapsed < timeout:
        try:
            run = hermes.get_run_status(run_id)
        except RuntimeError:
            run = {}

        status = run.get("status", "unknown")

        # Terminal states from Hermes API
        if status in ("failed", "error", "cancelled"):
            return status

        # Git-state settling heuristic
        if worktree_path:
            current_sig = _git_state_signature(worktree_path)
            if current_sig != last_sig:
                saw_change = True
                last_change_at = elapsed
                last_sig = current_sig
            elif saw_change and (elapsed - last_change_at) >= settle_interval:
                return "finished"
            elif not saw_change and elapsed >= stall_timeout:
                return "stalled"
        elif status == "completed":
            return "finished"

        await asyncio.sleep(poll_interval)
        elapsed += poll_interval

    return "timeout"


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
        payload = result.stdout
        return hashlib.sha1(payload.encode("utf-8", errors="ignore")).hexdigest()
    except Exception:
        return None


def _ensure_project_and_session(db_url: str, project_id: str, session_id: str, user_intent: str) -> None:
    """Ensure FK chain: projects -> sessions exists before node spawn."""
    import psycopg
    with psycopg.connect(db_url) as c:
        with c.cursor() as cur:
            cur.execute(
                "INSERT INTO projects (project_id, name, repo_path) "
                "VALUES (%s, %s, %s) ON CONFLICT (project_id) DO NOTHING",
                (project_id, project_id, f"/opt/aipc/conductor/workspace/{project_id}"),
            )
            cur.execute(
                "INSERT INTO sessions (session_id, project_id, user_intent, base_branch) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT (project_id, session_id) DO NOTHING",
                (session_id, project_id, user_intent, "main"),
            )
        c.commit()


def _update_session_runtime(session_id: str, worktree_path: str | None) -> None:
    if not worktree_path:
        return
    branch = os.path.basename(worktree_path).split(".", 1)[-1] if "." in os.path.basename(worktree_path) else None
    with queries.conn() as c, c.cursor() as cur:
        cur.execute(
            "UPDATE sessions SET worktree_path = %s, branch = %s WHERE session_id = %s",
            (worktree_path, branch, session_id),
        )


def _mark_node_done(session_id: str, node_id: str, node_commit_tag: str | None) -> None:
    with queries.conn() as c, c.cursor() as cur:
        cur.execute(
            """
            UPDATE tasks
               SET status = 'done',
                   completion_signal = 'watcher_done',
                   node_commit_tag = COALESCE(%s, node_commit_tag),
                   updated_at = NOW()
             WHERE session_id = %s AND node_id = %s
            """,
            (node_commit_tag, session_id, node_id),
        )
