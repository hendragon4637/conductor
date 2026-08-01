from __future__ import annotations

import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any

import psycopg

logger = logging.getLogger(__name__)

from backend.adapters.registry import get_adapter
from backend.aionui import AionUiClient
from backend.backends.opencode_config import spawn_env_for, write_worktree_config
from backend.backends.registry import is_self_orchestrating
from backend.db.queries import get_agent_config
from backend.orchestration.orchestrator_brief import build_orchestrator_brief, build_single_agent_lead_brief
from backend.skills import _make_engine, install_worktree_skills
from backend.worktree import WorktreeManager, assemble_for_spawn


ORCHESTRATOR_MODEL_PRIMARY = os.environ.get(
    "ORCHESTRATOR_MODEL_PRIMARY",
    "litellm/deepseek-planning",
)
ORCHESTRATOR_MODEL_FALLBACK = os.environ.get(
    "ORCHESTRATOR_MODEL_FALLBACK",
    "litellm/gptoss-exec",
)


def _normalize_model(model: str | None) -> str:
    return model or "litellm/gptoss-exec"


def _orchestrator_permission_profile() -> dict[str, Any]:
    return {
        "edit": "deny",
        "bash": "deny",
        "webfetch": "deny",
        "task": {"*": "allow"},
    }


def _member_permission_profile(cfg: dict[str, Any]) -> dict[str, Any]:
    return dict(cfg.get("permission_policy") or {"edit": "allow", "bash": {"*": "allow"}, "webfetch": "allow"})


def _node_id(node: dict[str, Any]) -> str:
    return str(node.get("id") or node.get("node_id") or "")


def spawn_node(
    node: dict[str, Any],
    plan: dict[str, Any],
    session_id: str,
    aionui: AionUiClient,
    wm: WorktreeManager,
    db_url: str | None = None,
    workspace_root: str | None = None,
    auto_approve: bool = True,
) -> str:
    """Execute one plan node: prepare worktree, spawn AionUi, return conversation id."""
    _db_url = db_url or os.environ.get("DATABASE_URL", "")
    wsr = workspace_root or os.environ.get("WORKSPACE_ROOT", "/opt/aipc/conductor/workspace")

    project_id = node.get("project_id", plan.get("project_id", "default"))
    agent_config_id = node["agent_config"]

    # 1. Resolve agent config
    cfg = get_agent_config(agent_config_id)
    if not cfg:
        raise ValueError(f"Agent config {agent_config_id} not found in DB")
    cli = cfg["cli"]

    # 2. Ensure worktree (re-use session worktree, or create one)
    if plan.get("worktree_path"):
        wt = Path(plan["worktree_path"])
    else:
        branch = _branch_for_session(session_id)
        wm.ensure_project(project_id)
        wt_path = wm.create(project_id, branch)
        wt = Path(wt_path)

    # 3. Assemble config into worktree
    assemble_for_spawn(
        worktree=wt,
        cli=cli,
        agent_config=cfg,
        project_id=project_id,
        session_id=session_id,
        db_url=_db_url,
        auto_approve=auto_approve,
        permission_rules=_member_permission_profile(cfg),
    )

    # 4. Spawn AionUi conversation
    adapter = get_adapter(cli)
    preset = adapter.aionui_preset_agent_type()
    assistant_id = adapter.aionui_assistant_id()
    model = _normalize_model(cfg.get("model_preference"))
    conv_id = aionui.create_conversation(
        preset_agent_type=preset,
        assistant_id=assistant_id,
        workspace=str(wt),
        model=model,
    )

    # 5. Ensure plan exists in DB, then create task + aionui_links rows
    _ensure_plan_in_db(_db_url, plan, project_id, session_id)
    task_id = _create_task(_db_url, plan, node, project_id, session_id)
    _create_aionui_link(_db_url, task_id, conv_id)

    # 6. Build and send prompt
    prompt = build_node_prompt(node, plan)
    aionui.send_message(conv_id, prompt)

    return conv_id


def spawn_team(
    nodes: list[dict[str, Any]],
    plan: dict[str, Any],
    session_id: str,
    aionui: AionUiClient,
    wm: WorktreeManager,
    db_url: str | None = None,
    workspace_root: str | None = None,
    auto_approve: bool = True,
) -> dict[str, str]:
    """Create a shared worktree + AionUi team for a multi-node plan DAG.

    Returns a dict mapping node_id -> conversation_id for each team member.
    All team members share the same worktree and project.
    """
    _db_url = db_url or os.environ.get("DATABASE_URL", "")
    wsr = workspace_root or os.environ.get("WORKSPACE_ROOT", "/opt/aipc/conductor/workspace")

    project_id = plan.get("project_id", "default")

    # 1. Create shared worktree (once for all nodes)
    _branch = _branch_for_session(session_id)
    wm.ensure_project(project_id)
    wt_path = wm.create(project_id, _branch)
    wt = Path(wt_path)
    plan["worktree_path"] = str(wt)

    first_cfg = None

    # Build team agent list and create DB records for each node
    team_agents = []
    node_conv_map: dict[str, str] = {}

    for node in nodes:
        agent_config_id = node["agent_config"]
        cfg = get_agent_config(agent_config_id)
        if not cfg:
            raise ValueError(f"Agent config {agent_config_id} not found in DB")
        if first_cfg is None:
            first_cfg = cfg

        cli = cfg["cli"]
        # Use the authoritative role from the DB config, not the node dict
        role_str = cfg["role"]
        # The orchestrator is always the team lead
        is_lead = role_str == "orchestrator" or (
            len(nodes) == 1 or node == nodes[0]
        )

        model = cfg.get("model_preference") or "nvidia/openai/gpt-oss-120b"

        # Use agent_config_id + role for a descriptive member label
        member_name = f"{cfg['agent_config_id']} ({cfg['role']})"
        team_agents.append({
            "name": member_name,
            "role": "lead" if is_lead else "teammate",
            "backend": cli,
            "model": model,
        })

    # 2. Assemble config into worktree (once, using first agent's config as base)
    if first_cfg:
        cli = first_cfg["cli"]
        assemble_for_spawn(
            worktree=wt,
            cli=cli,
            agent_config=first_cfg,
            project_id=project_id,
            session_id=session_id,
            db_url=_db_url,
            auto_approve=auto_approve,
        )

    # 3. Ensure plan exists in DB
    _ensure_plan_in_db(_db_url, plan, project_id, session_id)

    # 4. Create AionUi team with descriptive names
    team_title = plan.get("title", plan["plan_id"])
    project_prefix = f"[{project_id}]" if project_id else ""
    team_data = aionui.create_team(
        name=f"{project_prefix} {team_title}".strip(),
        workspace=str(wt),
        agents=team_agents,
    )

    # 5. Extract conversation IDs and slot_ids for each agent from the team response
    # Response shape: { id, name, workspace, agents: [{ name, role, slot_id, conversation_id }] }
    team_agents_result = team_data.get("agents", [])
    team_id = team_data.get("id", "")
    conv_to_slot: dict[str, str] = {}
    for i, node in enumerate(nodes):
        if i < len(team_agents_result):
            agent_info = team_agents_result[i]
            conv_id = agent_info.get("conversation_id", "")
            slot_id = agent_info.get("slot_id", "")
            conv_to_slot[conv_id] = slot_id
            node_conv_map[_node_id(node)] = conv_id

    # 6. Create task + aionui_links rows for each node
    for node in nodes:
        conv_id = node_conv_map.get(_node_id(node))
        if not conv_id:
            continue
        task_id = _create_task(_db_url, plan, node, project_id, session_id)
        _create_aionui_link(_db_url, task_id, conv_id)

    # 7. Build team roster for orchestrator DAG context
    #    Exclude the orchestrator itself — it only needs info about its team members.
    #    Pre-resolve depends_on node IDs to human-readable role names.
    node_id_to_role: dict[str, str] = {}
    for node in nodes:
        cfg_node = get_agent_config(node["agent_config"])
        node_id_to_role[_node_id(node)] = (cfg_node or {}).get("role", node.get("role", "?"))

    team_info = []
    for node in nodes:
        is_orch_node = (
            node.get("agent_config") == "orchestrator"
            or node.get("role") == "orchestrator"
        )
        if is_orch_node:
            continue
        raw_deps = node.get("depends_on", [])
        deps_resolved = [node_id_to_role.get(d, d) for d in raw_deps]
        team_info.append({
            "agent_config_id": node.get("agent_config", ""),
            "role": node.get("role", ""),
            "task": node.get("task", ""),
            "success": node.get("success", ""),
            "depends_on": deps_resolved,
        })

    # 8. Send prompt ONLY to the orchestrator (single entry point).
    #     All other team members sit idle until the orchestrator delegates to them.
    for node in nodes:
        conv_id = node_conv_map.get(_node_id(node))
        if not conv_id:
            continue
        is_orch = (
            node.get("agent_config") == "orchestrator"
            or node.get("role") == "orchestrator"
        )
        if not is_orch:
            continue
        prompt = build_node_prompt(node, plan, team_info=team_info)
        slot_id = conv_to_slot.get(conv_id, "")
        try:
            if team_id and slot_id:
                aionui.send_team_message(team_id, slot_id, prompt)
            else:
                aionui.send_message(conv_id, prompt)
        except Exception as e:
            print(f"  [spawn] Failed to send prompt to {_node_id(node)}: {e}")

    return node_conv_map


def _ensure_plan_in_db(db_url: str, plan: dict[str, Any], project_id: str, session_id: str | None = None) -> None:
    """Insert or update the plan (v5.1: no session_id column).
    
    Auto-creates the project row if missing (defense-in-depth).
    """
    with psycopg.connect(db_url) as c:
        with c.cursor() as cur:
            row = cur.execute(
                "SELECT system_id FROM projects WHERE project_id = %s", (project_id,)
            ).fetchone()
            if not row:
                cur.execute(
                    "INSERT INTO systems (system_id, name, description) "
                    "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                    (project_id, project_id, f"Auto-created system-of-one for {project_id}"),
                )
                cur.execute(
                    "INSERT INTO projects (project_id, name, repo_path, system_id) VALUES (%s, %s, %s, %s) "
                    "ON CONFLICT (project_id) DO NOTHING",
                    (project_id, project_id, f"/opt/aipc/conductor/workspace/{project_id}", project_id),
                )
            nodes = plan.get("dag", plan.get("nodes", []))
            user_intent = plan.get("user_intent", "")
            goal = plan.get("goal", user_intent)
            success = plan.get("success", {})
            if isinstance(success, str):
                success = {"text": success}
            cur.execute(
                """INSERT INTO plans
                   (plan_id, project_id, user_intent, goal, success, dag,
                    ratified)
                   VALUES (%s, %s, %s, %s, %s, %s, TRUE)
                   ON CONFLICT (plan_id) DO NOTHING
                """,
                (
                    plan["plan_id"],
                    project_id,
                    user_intent,
                    goal,
                    json.dumps(success),
                    json.dumps(nodes),
                ),
            )
        c.commit()


def _branch_for_session(session_id: str) -> str:
    """Derive a unique branch name from session id."""
    safe = session_id.replace("/", "-").replace("_", "-")
    return f"feat/{safe}"


def _create_task(db_url: str, plan: dict[str, Any], node: dict[str, Any], project_id: str, session_id: str) -> uuid.UUID:
    task_id = uuid.uuid4()
    with psycopg.connect(db_url) as c:
        with c.cursor() as cur:
            cur.execute(
                """INSERT INTO tasks
                   (task_id, project_id, session_id, user_intent, status,
                    plan_id, node_id, created_by)
                   VALUES (%s, %s, %s, %s, 'open', %s, %s, 'conductor')
                """,
                (str(task_id), project_id, session_id,
                 plan.get("user_intent", ""), plan.get("plan_id"),
                 _node_id(node)),
            )
        c.commit()
    return task_id


def _aion_files_block(worktree: Path) -> str:
    """``[[AION_FILES]]`` block referencing ``.conductor/NODE_BRIEF.md`` for the executor."""
    brief_path = worktree / ".conductor" / "NODE_BRIEF.md"
    if brief_path.exists():
        return f"\n\n[[AION_FILES]]\n{brief_path.absolute()}\n"
    return ""


def _create_aionui_link(db_url: str, task_id: uuid.UUID, conversation_id: str) -> None:
    with psycopg.connect(db_url) as c:
        with c.cursor() as cur:
            cur.execute(
                """INSERT INTO aionui_links
                   (link_id, task_id, aionui_conversation_id, status)
                   VALUES (%s, %s, %s, 'spawned')
                   ON CONFLICT (link_id) DO NOTHING
                """,
                (conversation_id, str(task_id), conversation_id),
            )
        c.commit()


def _spawn_single_member_team(
    node: dict[str, Any],
    plan: dict[str, Any],
    session_id: str,
    aionui: AionUiClient,
    wm: WorktreeManager,
    member_id: str,
    dep_context: str,
    db_url: str | None = None,
    workspace_root: str | None = None,
    auto_approve: bool = True,
) -> dict[str, str]:
    _db_url = db_url or os.environ.get("DATABASE_URL", "")
    project_id = plan.get("project_id", "default")

    if plan.get("worktree_path"):
        wt = Path(plan["worktree_path"])
    else:
        branch = _branch_for_session(session_id)
        wm.ensure_project(project_id)
        wt_path = wm.create(project_id, branch)
        wt = Path(wt_path)
        plan["worktree_path"] = str(wt)

    cfg = get_agent_config(member_id)
    if not cfg:
        raise ValueError(f"Agent config {member_id} not found in DB")

    engine = cfg.get("cli") or (cfg.get("execution") or {}).get("backend", "opencode")
    assemble_for_spawn(
        worktree=wt,
        cli=engine,
        agent_config=cfg,
        project_id=project_id,
        session_id=session_id,
        db_url=_db_url,
        auto_approve=auto_approve,
        permission_rules=_member_permission_profile(cfg),
    )

    _ensure_plan_in_db(_db_url, plan, project_id, session_id)

    # Single member nodes use a regular conversation instead of a team.
    # This avoids AionUi v2.1.33 team lifecycle (team_tasks, slot_id
    # routing) and lets the watcher detect completion via normal
    # conversation idle-time signals.
    try:
        adapter = get_adapter(engine)
        preset = adapter.aionui_preset_agent_type()
        assistant_id = adapter.aionui_assistant_id()
    except (ImportError, ValueError):
        preset = "acp"
        assistant_id = None
    conv_id = aionui.create_conversation(
        preset_agent_type=preset,
        assistant_id=assistant_id,
        workspace=str(wt),
        model=_normalize_model(cfg.get("model_preference")),
    )

    task_id = _create_task(_db_url, plan, node, project_id, session_id)
    if conv_id:
        _create_aionui_link(_db_url, task_id, conv_id)
        brief = build_single_agent_lead_brief(
            node=node, dep_context=dep_context,
            goal=plan.get("user_intent", ""),
        )
        brief += _aion_files_block(wt)
        aionui.send_message(conv_id, brief)

    return {member_id: conv_id} if conv_id else {}


def spawn_node_team(
    node: dict[str, Any],
    plan: dict[str, Any],
    session_id: str,
    aionui: AionUiClient,
    wm: WorktreeManager,
    members: list[str] | None = None,
    dep_context: str = "",
    db_url: str | None = None,
    workspace_root: str | None = None,
    auto_approve: bool = True,
) -> dict[str, str]:
    """Create an AionUi team for a single node = orchestrator + members.

    Unlike ``spawn_team`` (which creates ONE team for ALL plan nodes),
    this function creates a dedicated team per node. Each node gets:
      - The built-in orchestrator agent (always lead)
      - The node's specialist members (from the ``members`` list)

    Conductor sends ONE brief to the orchestrator containing the goal,
    team roster, and any dependency handoff context.

    Returns a dict mapping agent name -> conversation_id.
    ``{'orchestrator': '<conv_id>', '<member1>': '<conv_id>', ...}``
    """
    _db_url = db_url or os.environ.get("DATABASE_URL", "")
    wsr = workspace_root or os.environ.get("WORKSPACE_ROOT", "/opt/aipc/conductor/workspace")

    project_id = plan.get("project_id", "default")
    _proj_dir = Path(wsr) / project_id
    raw_members = members or [node.get("agent_config", "opencode:backend-executor")]
    node_members = [
        m.get("agent_config", "opencode:backend-executor") if isinstance(m, dict) else (str(m) if not isinstance(m, str) else m)
        for m in raw_members
    ]

    # 1. Resolve worktree
    if plan.get("worktree_path"):
        wt = Path(plan["worktree_path"])
    else:
        branch = _branch_for_session(session_id)
        wm.ensure_project(project_id)
        wt_path = wm.create(project_id, branch)
        wt = Path(wt_path)
        plan["worktree_path"] = str(wt)

        # Capture master_commit for the run (fires once per run — first node creates worktree)
        try:
            import subprocess as _sp
            _proj_dir = Path(wsr) / project_id
            _res = _sp.run(
                ["git", "-C", str(_proj_dir), "rev-parse", "HEAD"],
                capture_output=True, text=True, timeout=15, check=True,
            )
            _master_commit = _res.stdout.strip()
            from backend.db.queries import conn as _db_conn
            with _db_conn() as _c:
                _c.execute(
                    "UPDATE runs SET master_commit = %s WHERE id = %s",
                    (_master_commit, session_id),
                )
        except Exception:
            pass

    # Materialize dependencies (deps/ directory)
    try:
        import psycopg
        from psycopg.rows import dict_row
        _db = os.environ.get("DATABASE_URL", "")
        if _db:
            with psycopg.connect(_db, row_factory=dict_row) as _conn:
                with _conn.cursor() as _cur:
                    _cur.execute(
                        "SELECT system_id, kind FROM projects WHERE project_id = %s",
                        (project_id,),
                    )
                    _sys_row = _cur.fetchone()
                    if _sys_row and _sys_row["system_id"]:
                        _dep_mode = "artifacts" if _sys_row["kind"] == "assembly" else "source"
                        wm.materialize_deps(project_id, _sys_row["system_id"], mode=_dep_mode, worktree_path=str(wt))
        from services.planner.system_goal import record_dep_shas
        record_dep_shas(project_id, session_id)
    except Exception as exc:
        logger.warning("Failed to materialize deps for %s: %s", project_id, exc)

    # Inject domain standard scaffolding into worktree (pre-spawn)
    try:
        from backend.standards.loader import get_standard
        from backend.planning.harness_worktree import read_project_manifest

        # Resolve standard slug by subdir FIRST (from project manifest),
        # then fall back to capability-based heuristic for legacy plans.
        _node_subdir = node.get("subdir", "")
        _manifest = read_project_manifest(_proj_dir)
        _slug_candidates: list[str] = []

        if _manifest and _node_subdir:
            # Subdir-first: find component in manifest
            for _mc in _manifest.get("components", []):
                if _mc.get("subdir") == _node_subdir:
                    _slug = _mc.get("standard_slug", "")
                    if _slug:
                        _slug_candidates.append(_slug)
                    break

        if not _slug_candidates:
            # Fallback: derive from capability families or plan domain
            _cap_families: set[str] = set()
            for cap in (node.get("capabilities") or []):
                if isinstance(cap, dict):
                    for fam in (cap.get("family") or []):
                        _cap_families.add(str(fam))
                elif isinstance(cap, str):
                    _cap_families.add(cap)

            _domain = plan.get("domain", "")
            if "backend" in _cap_families or "backend" in _domain.lower():
                _slug_candidates.append("python-backend")
            if "frontend" in _cap_families or "frontend" in _domain.lower():
                _slug_candidates.append("react-frontend")

        for slug in _slug_candidates:
            std = get_standard(slug)
            if not std:
                continue
            tree = std.get("scaffold_tree") or []
            for entry in tree:
                src = entry.get("source", "")
                dst_path = entry.get("path", "")
                if src and dst_path:
                    src_p = Path(src)
                    dst_p = wt / dst_path
                    if src_p.exists() and not dst_p.exists():
                        dst_p.parent.mkdir(parents=True, exist_ok=True)
                        if src_p.is_file():
                            dst_p.write_bytes(src_p.read_bytes())
                        elif src_p.is_dir():
                            import shutil
                            shutil.copytree(src_p, dst_p, dirs_exist_ok=True)

            manifest = std.get("tool_manifest")
            if manifest:
                (wt / ".conductor").mkdir(parents=True, exist_ok=True)
                (wt / ".conductor" / "tool_manifest.json").write_text(
                    json.dumps(manifest, indent=2) + "\n"
                )

            logger.info("Injected %s scaffolding into worktree %s", slug, wt)
    except Exception as exc:
        logger.debug("Standard scaffolding injection skipped: %s", exc)

    # ── Assembly service descriptor: emit workspace.json for assembly projects ──
    try:
        import psycopg
        _db_url_a = os.environ.get("DATABASE_URL", "")
        if _db_url_a:
            with psycopg.connect(_db_url_a) as _ca:
                with _ca.cursor() as _cu:
                    _cu.execute(
                        "SELECT kind, system_id FROM projects WHERE project_id = %s",
                        (project_id,),
                    )
                    _prow = _cu.fetchone()
                    if _prow and _prow[0] == "assembly" and _prow[1]:
                        from backend.assembly.generator import generate_assembly
                        _assy_result = generate_assembly(_prow[1])
                        if not _assy_result.get("errors"):
                            _svc_desc = {
                                "system_id": _prow[1],
                                "services": [
                                    {
                                        "name": s["name"],
                                        "slug": s["slug"],
                                        "port": s.get("assigned_host_port", 8000),
                                        "dep_shas": s.get("dep_shas", {}),
                                        "depends_on": s.get("depends_on", []),
                                    }
                                    for s in _assy_result.get("services", [])
                                ],
                            }
                            (wt / "workspace.json").write_text(
                                json.dumps(_svc_desc, indent=2) + "\n"
                            )
                            logger.info(
                                "Emitted workspace.json for assembly %s (%d services)",
                                project_id, len(_svc_desc["services"]),
                            )
    except Exception as exc:
        logger.debug("Assembly service descriptor skipped: %s", exc)

    # Install capability-scoped skills into worktree (pre-spawn)
    try:
        engine = _make_engine(_db_url)
        install_worktree_skills(engine, str(wt), node)
    except Exception as exc:
        logger.warning("Failed to install worktree skills: %s — continuing", exc)

    # ── Class-a (self-orchestrating) short-circuit ──────────────────────────
    backend_key = node.get("backend", "opencode")
    # Prefer member-level backend when present (LLM sometimes puts
    # backend on the member rather than the node).
    if raw_members:
        for m in raw_members:
            if isinstance(m, dict) and m.get("backend"):
                backend_key = str(m["backend"])
                break
    if is_self_orchestrating(backend_key):
        return _spawn_self_orchestrating(
            node=node,
            plan=plan,
            session_id=session_id,
            aionui=aionui,
            wm=wm,
            backend_key=backend_key,
            db_url=db_url,
            workspace_root=workspace_root,
            auto_approve=auto_approve,
        )

    if len(node_members) == 1:
        return _spawn_single_member_team(
            node=node,
            plan=plan,
            session_id=session_id,
            aionui=aionui,
            wm=wm,
            member_id=node_members[0],
            dep_context=dep_context,
            db_url=db_url,
            workspace_root=workspace_root,
            auto_approve=auto_approve,
        )

    # 2. Build team agent list: orchestrator + members
    team_agents = []

    # Orchestrator is always first (lead)
    orch_cfg = get_agent_config("orchestrator")
    if not orch_cfg:
        raise ValueError("Built-in orchestrator agent_config not found in DB")
    team_agents.append({
        "name": "orchestrator (lead)",
        "role": "lead",
        "backend": orch_cfg["cli"],
        "model": _normalize_model(ORCHESTRATOR_MODEL_PRIMARY or orch_cfg.get("model_preference")),
    })

    # Member agents
    member_configs = []
    for member_id in node_members:
        cfg = get_agent_config(member_id)
        if not cfg:
            raise ValueError(f"Agent config {member_id} not found in DB")
        member_configs.append(cfg)
        team_agents.append({
            "name": f"{cfg['agent_config_id']} ({cfg['role']})",
            "role": "teammate",
            "backend": cfg["cli"],
            "model": _normalize_model(cfg.get("model_preference")),
        })

    # 3. Assemble worktree config using the first member's config as base.
    # Current limitation: the team shares one worktree-level permission file,
    # so the member-capable profile is applied there. The stricter
    # orchestrator-only boundary is enforced through the orchestrator brief.
    if member_configs:
        first_cfg = member_configs[0]
        assemble_for_spawn(
            worktree=wt,
            cli=first_cfg["cli"],
            agent_config=first_cfg,
            project_id=project_id,
            session_id=session_id,
            db_url=_db_url,
            auto_approve=auto_approve,
            permission_rules=_member_permission_profile(first_cfg),
        )

    # 4. Ensure plan in DB
    _ensure_plan_in_db(_db_url, plan, project_id, session_id)

    # 5. Create AionUi team
    team_title = plan.get("title", plan["plan_id"])
    project_prefix = f"[{project_id}]" if project_id else ""
    team_data = aionui.create_team(
        name=f"{project_prefix} {team_title} — node {node.get('id', '?')}".strip(),
        workspace=str(wt),
        agents=team_agents,
    )

    # 6. Extract conversation IDs, slot_ids + team ID
    team_id = team_data.get("id", "")
    conv_map: dict[str, str] = {}
    conv_to_slot: dict[str, str] = {}
    team_agents_result = team_data.get("agents", [])
    for i, agent_info in enumerate(team_agents_result):
        conv_id = agent_info.get("conversation_id", "")
        slot_id = agent_info.get("slot_id", "")
        conv_to_slot[conv_id] = slot_id
        if i == 0:
            conv_map["orchestrator"] = conv_id
        elif (i - 1) < len(node_members):
            conv_map[node_members[i - 1]] = conv_id

    # 7. Create DB records
    task_id = _create_task(_db_url, plan, node, project_id, session_id)
    orch_conv_id = conv_map.get("orchestrator", "")
    if orch_conv_id:
        _create_aionui_link(_db_url, task_id, orch_conv_id)

    # 8. Build and send ONE brief to the orchestrator
    prompt = build_orchestrator_brief(
        node=node,
        members=[
            {
                "agent_config_id": cfg.get("agent_config_id", ""),
                "role": cfg.get("role", ""),
                "task": node.get("task") or node.get("description") or node.get("title", ""),
                "success": node.get("success") or node.get("success_criterion", ""),
                "depends_on": node.get("depends_on", []),
            }
            for cfg in member_configs
        ],
        dep_context=dep_context,
        goal=plan.get("user_intent", ""),
    )
    if orch_conv_id:
        prompt += _aion_files_block(wt)
        slot_id = conv_to_slot.get(orch_conv_id, "")
        try:
            if team_id and slot_id:
                aionui.send_team_message(team_id, slot_id, prompt)
            else:
                aionui.send_message(orch_conv_id, prompt)
        except Exception as e:
            print(f"  [spawn] Failed to send prompt to orchestrator: {e}")

    if team_id:
        conv_map["__team_id__"] = team_id
    return conv_map


def _spawn_self_orchestrating(
    node: dict[str, Any],
    plan: dict[str, Any],
    session_id: str,
    aionui: AionUiClient,
    wm: WorktreeManager,
    backend_key: str,
    db_url: str | None = None,
    workspace_root: str | None = None,
    auto_approve: bool = True,
) -> dict[str, str]:
    """Spawn a self-orchestrating (class-a) backend — no Conductor orchestrator.

    Class-a backends (Hermes, opencode_omo) are their own leader.

    - **Hermes (direct HTTP):** Calls the Hermes HTTP API directly to create
      a run — no AionUi conversation.  The Hermes run_id is returned in
      ``conv_map["hermes"]`` and ``conv_map["__run_id__"]``.
    - **opencode_omo:** Creates an AionUi conversation with the OpenCode
      preset, writes per-worktree config, and sends the goal-only brief.

    Returns:
        conv_map with NO ``"orchestrator"`` key — only the member entry
        (and ``__run_id__`` for Hermes).
    """
    _db_url = db_url or os.environ.get("DATABASE_URL", "")
    wsr = workspace_root or os.environ.get("WORKSPACE_ROOT", "/opt/aipc/conductor/workspace")
    project_id = plan.get("project_id", "default")

    # 1. Resolve worktree (reuse if plan already has one)
    if plan.get("worktree_path"):
        wt = Path(plan["worktree_path"])
    else:
        branch = _branch_for_session(session_id)
        wm.ensure_project(project_id)
        wt_path = wm.create(project_id, branch)
        wt = Path(wt_path)
        plan["worktree_path"] = str(wt)

    # 3. Ensure plan exists in DB
    _ensure_plan_in_db(_db_url, plan, project_id, session_id)

    goal_brief = f"Goal: {plan.get('user_intent', '')}\n\nTask: {node.get('task', '')}\n\nSuccess criterion: {node.get('success', '')}"
    conv_map: dict[str, str] = {}
    conv_id = ""

    if backend_key == "hermes":
        # Hermes runs in Docker: /workspace on the container maps to the
        # workspace root on the host.  The Conductor worktree is a subdir
        # of the workspace root — compute the container-relative path.
        _ws_root = Path("/opt/aipc/conductor/workspace")
        try:
            _wt_rel = str(wt.relative_to(_ws_root))
        except ValueError:
            _wt_rel = ""
        _container_wt = f"/workspace/{_wt_rel}" if _wt_rel else ""
        goal_brief = (
            f"Goal: {plan.get('user_intent', '')}\n\n"
            f"Task: {node.get('task', '')}\n"
            f"IMPORTANT: Write ALL deliverables inside {_container_wt}/ "
            f"and use absolute paths like {_container_wt}/your_file\n\n"
            f"Success criterion: {node.get('success', '')}"
        ) if _wt_rel else (
            f"Goal: {plan.get('user_intent', '')}\n\n"
            f"Task: {node.get('task', '')}\n\n"
            f"Success criterion: {node.get('success', '')}"
        )
        print(f"[PRINT] Hermes spawn: session_id={session_id} wt={wt} _wt_rel={_wt_rel} _container_wt={_container_wt}", flush=True)
        print(f"[PRINT] Hermes spawn: goal_brief (first 200 chars): {goal_brief[:200]}", flush=True)
        from backend.hermes_adapter import HermesClient

        hermes = HermesClient()
        print(f"[PRINT] Hermes spawn: calling create_run(goal=..., worktree={_container_wt or str(wt)})", flush=True)
        run_resp = hermes.create_run(goal=goal_brief, worktree=_container_wt or str(wt))
        print(f"[PRINT] Hermes spawn: create_run response={run_resp}", flush=True)
        run_id = run_resp.get("run_id", "")
        if not run_id:
            raise RuntimeError(f"Hermes API did not return a run_id: {run_resp}")
        conv_map["hermes"] = run_id
        conv_map["__run_id__"] = run_id
    else:
        # opencode_omo — write per-worktree config, create AionUi conversation
        write_worktree_config(
            worktree=wt,
            agent_type="opencode_omo",
            appended_prompt=str(node.get("task", "")),
        )
        preset_type = "acp"
        adapter = None
        try:
            from backend.adapters.registry import get_adapter

            adapter = get_adapter("opencode")
            preset_type = adapter.aionui_preset_agent_type()
        except (ImportError, ValueError):
            pass
        assistant_id = adapter.aionui_assistant_id() if adapter else None

        conv_id = aionui.create_conversation(
            preset_agent_type=preset_type,
            assistant_id=assistant_id,
            workspace=str(wt),
        )
        conv_map[backend_key] = conv_id
        aionui.send_message(conv_id, goal_brief + _aion_files_block(wt))

        # Set OMO env
        env = spawn_env_for("opencode_omo")
        os.environ.update(env)

    # 5. Create DB records
    task_id = _create_task(_db_url, plan, node, project_id, session_id)
    if conv_id:
        _create_aionui_link(_db_url, task_id, conv_id)

    return conv_map


def _build_orchestrator_prompt(
    node: dict[str, Any],
    plan: dict[str, Any],
    member_configs: list[dict[str, Any]],
    dep_context: str = "",
) -> str:
    """Build the brief sent to the orchestrator for this node's team."""
    parts = [
        f"Goal: {plan.get('user_intent', '')}",
        f"Role: orchestrator",
        f"This node's task: {node.get('task', '')}",
        f"Success criterion: {node.get('success', '')}",
        "",
        "INSTRUCTION: You are the team leader. Do NOT perform this task yourself. "
        "Delegate the work to your team members listed below. Coordinate their efforts, "
        "review their outputs, and ensure the success criterion is met.",
    ]

    if dep_context:
        parts.append(f"\n[DEPENDENCY CONTEXT]\n{dep_context}")

    if member_configs:
        roster_lines = ["", "[TEAM ROSTER]"]
        for i, mc in enumerate(member_configs, 1):
            mc_role = mc.get("role", "?")
            mc_id = mc.get("agent_config_id", "?")
            roster_lines.append(
                f"\n{i}. {mc_role} ({mc_id})"
            )
        roster_lines.append("")
        parts.append("\n".join(roster_lines))

    return "\n\n".join(parts)


def _resolve_dep_roles(dep_ids: list[str], team_info: list[dict[str, Any]]) -> str:
    """Map raw node/agent IDs to human-readable roles for the orchestrator."""
    if not dep_ids:
        return ""
    resolved = []
    for did in dep_ids:
        matched = [tm.get("role", "?") for tm in team_info if tm.get("agent_config_id") == did]
        if matched:
            resolved.append(matched[0])
        else:
            resolved.append(did)
    return ", ".join(resolved)


def build_node_prompt(
    node: dict[str, Any],
    plan: dict[str, Any],
    team_info: list[dict[str, Any]] | None = None,
) -> str:
    """Build the instruction sent to AionUi for this node.

    The orchestrator (team leader) receives the full goal and the
    complete DAG context (team roster with each member's role, task,
    success criterion, and dependency order).  Other members receive
    no initial message — the orchestrator delegates to them.
    """
    agent_config_id = node.get("agent_config", "")
    role = node.get("role", "")
    if agent_config_id == "orchestrator" or role == "orchestrator":
        parts = [
            f"Goal: {plan.get('user_intent', '')}",
            f"Role: orchestrator",
            f"This node's task: {node.get('task', '')}",
            f"Success criterion: {node.get('success', '')}",
            "",
            "INSTRUCTION: You are the team leader. Do NOT perform this task yourself. "
            "Delegate the work to your team members listed below. Coordinate their efforts, "
            "review their outputs, and ensure the success criterion is met.",
        ]
        if team_info:
            roster_lines = ["", "[TEAM ROSTER]"]
            for i, tm in enumerate(team_info, 1):
                tm_role = tm.get("role", "?")
                tm_ac = tm.get("agent_config_id", "?")
                tm_task = tm.get("task", "")
                tm_success = tm.get("success", "")
                tm_deps = tm.get("depends_on", [])
                dep_hint = "(starts first)" if not tm_deps else f"(waits for {_resolve_dep_roles(tm_deps, team_info)})"
                roster_lines.append(
                    f"\n{i}. {tm_role} ({tm_ac})"
                    f"\n   Task: {tm_task}"
                    f"\n   Success criterion: {tm_success}"
                    f"\n   Order: {dep_hint}"
                )
            roster_lines.append("")
            parts.append("\n".join(roster_lines))
        return "\n\n".join(parts)
    else:
        parts = [
            f"Role: {role}",
            f"Success criterion: {node.get('success', '')}",
        ]
        return "\n\n".join(parts)
