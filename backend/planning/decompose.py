"""Decomposition engine — plan intent → decomposed chunk DAG.

Exposes a single lifecycle function ``decompose_or_update`` callable from
all 5 entry points:
  1. chat_promote  — promote-from-chat thread
  2. new_plan      — new plan created in the Plan tab
  3. refine        — refine existing plan with a natural-language instruction
  4. append_node   — add a node to an existing plan (incremental)
  5. cross_project — add a cross-project node
  6. trigger       — cron/scheduled run_task

Supersedes the three-tier kind from Files 17-18. Every decomposed chunk
is a team led by the built-in orchestrator.
"""
from __future__ import annotations

import json
from typing import Any, Callable, Literal

from backend.evaluator import generate_checks
from backend.planning.schema import NodeSuccess
from backend.planning.decomposed_spec import (
    DecomposedPlan,
    ChunkNode,
    validate_decomposed,
)
from backend.evaluator.schema import Check
from backend.planning.model_selector import select_brain_model
from backend.planning.brain import _default_llm, _generate_plan_id

SourceType = Literal["chat_promote", "new_plan", "refine", "append_node",
                      "cross_project", "trigger", "byo_dag"]


def _next_node_id(existing: list[ChunkNode] | list[dict]) -> str:
    """Generate the next available node-id for incremental appends."""
    existing_ids = {c.id if hasattr(c, 'id') else c.get('id', '?') for c in existing}
    i = 1
    while f"node-{i}" in existing_ids:
        i += 1
    return f"node-{i}"


def _decompose_from_supplied_dag(
    plan_id: str,
    nodes_raw: list[dict[str, Any]],
    quality_intent: str | None = None,
    project_id: str = "default",
) -> DecomposedPlan:
    """BYO-DAG: validate supplied nodes, convert to ChunkNodes, generate checks.

    Skips the brain entirely. Validates:
    - Every node has >=1 member with backend
    - Dependencies resolve to existing node IDs
    - DAG is acyclic
    Then generates eval checks per node using member role and quality_intent.

    Args:
        plan_id: Target plan ID.
        nodes_raw: List of dicts from BYO-DAG (canonical NodeSpec dicts).
        quality_intent: Free-text quality intent forwarded to generate_checks.
        project_id: Target project.

    Returns:
        A validated ``DecomposedPlan``.

    Raises:
        ValueError on invalid DAG.
    """
    # ── Validate DAG ────────────────────────────────────────────────
    node_ids: set[str] = set()
    for i, n in enumerate(nodes_raw):
        nid = n.get("id") or n.get("node_id") or f"node-{i + 1}"
        if nid in node_ids:
            raise ValueError(f"BYO-DAG: duplicate node id '{nid}'")
        node_ids.add(nid)

    all_ids: set[str] = set()
    for i, n in enumerate(nodes_raw):
        nid = n.get("id") or n.get("node_id") or f"node-{i + 1}"
        all_ids.add(nid)
        members_raw = n.get("members", [])
        if not members_raw:
            raise ValueError(f"BYO-DAG node '{nid}': must have >=1 member with backend")
        for m in members_raw:
            if isinstance(m, dict) and not m.get("backend"):
                raise ValueError(f"BYO-DAG node '{nid}': member {m.get('agent_config', '?')} missing backend")
        for dep in n.get("depends_on", []):
            pass  # resolve below

    # Deps resolve
    for i, n in enumerate(nodes_raw):
        nid = n.get("id") or n.get("node_id") or f"node-{i + 1}"
        for dep in n.get("depends_on", []):
            if dep not in all_ids:
                raise ValueError(f"BYO-DAG node '{nid}': depends_on '{dep}' not found")

    # Acyclicity
    adj: dict[str, list[str]] = {nid: [] for nid in all_ids}
    for i, n in enumerate(nodes_raw):
        nid = n.get("id") or n.get("node_id") or f"node-{i + 1}"
        for dep in n.get("depends_on", []):
            adj.setdefault(dep, []).append(nid)
    visited: set[str] = set()
    stack: set[str] = set()

    def _dfs(nid: str) -> None:
        if nid in stack:
            raise ValueError(f"BYO-DAG: cycle detected involving node '{nid}'")
        if nid in visited:
            return
        visited.add(nid)
        stack.add(nid)
        for neighbor in adj.get(nid, []):
            _dfs(neighbor)
        stack.remove(nid)

    for nid in all_ids:
        if nid not in visited:
            _dfs(nid)

    # ── Convert to ChunkNodes + generate checks ─────────────────────
    total = len(nodes_raw)
    chunks: list[ChunkNode] = []
    for i, n in enumerate(nodes_raw):
        nid = n.get("id") or n.get("node_id") or f"node-{i + 1}"
        members_raw = n.get("members", [])
        members = [
            m.get("agent_config") if isinstance(m, dict) else m
            for m in members_raw
        ]
        task_raw = n.get("task", {})
        if isinstance(task_raw, dict):
            task_text = task_raw.get("text", n.get("description", n.get("title", "")))
        else:
            task_text = n.get("description") or n.get("title") or ""

        success_raw = n.get("success", {})
        if isinstance(success_raw, dict):
            success_text = success_raw.get("text", "")
        else:
            success_text = n.get("success_criterion") or task_text

        chunk = ChunkNode(
            id=nid,
            members=members,
            depends_on=n.get("depends_on", []),
            success=NodeSuccess(text=success_text),
        )

        # Generate checks
        per_node_extra: list = []
        try:
            from backend.evaluator.memory_integration import ground_checks_with_memory as _ground
            for agent_id in members:
                recalled = _ground(task=success_text, project=project_id, agent=agent_id)
                per_node_extra.extend(recalled)
        except Exception:
            pass

        generated = generate_checks(
            node_id=nid,
            task=success_text,
            success_criterion=success_text,
            node_index=i,
            total_nodes=total,
            extra_checks=per_node_extra,
            quality_intent=quality_intent,
            members=members,
        )
        chunk.checks = generated.checks
        chunks.append(chunk)

    dplan = DecomposedPlan(
        plan_id=plan_id,
        worktree_root="/opt/aipc/conductor/workspace",
        chunks=chunks,
    )
    validate_decomposed(dplan)
    return dplan


def decompose_or_update(
    plan_id: str,
    source: SourceType,
    payload: dict[str, Any],
    existing_chunks: list[ChunkNode] | None = None,
    available_agent_configs: list[dict[str, Any]] | None = None,
    llm_call: Callable[[str], str] | None = None,
) -> DecomposedPlan:
    """Single lifecycle function callable from all decomposition entry points.

    Args:
        plan_id: The plan ID this decomposition belongs to.
        source: One of the 5 entry points.
        payload: Dict with source-specific keys:
            - chat_promote / new_plan / trigger:
                ``{"intent": str, "project_id": str?, "context": dict?}``
            - refine:
                ``{"instruction": str, "existing_nodes": list[dict]?}``
            - append_node / cross_project:
                ``{"members": list[str], "depends_on": list[str],
                   "task": str, "success_criterion": str?,
                   "project_id": str?, "target_project": str?}``
        existing_chunks: Current chunks for incremental operations.
        available_agent_configs: List of agent config dicts for LLM context.
        llm_call: Optional LLM call function (defaults to brain model).

    Returns:
        A validated ``DecomposedPlan``.

    Raises:
        ValueError on invalid source payload or failed validation.
    """
    if llm_call is None:
        llm_call = _default_llm

    cfgs = available_agent_configs or []
    existing = existing_chunks or []

    if source == "byo_dag":
        nodes_raw = payload.get("nodes", [])
        if not nodes_raw:
            raise ValueError("BYO-DAG source requires payload['nodes'] (non-empty list)")
        quality_intent = payload.get("quality_intent")
        project_id = payload.get("project_id", "default")
        return _decompose_from_supplied_dag(
            plan_id=plan_id,
            nodes_raw=nodes_raw,
            quality_intent=quality_intent,
            project_id=project_id,
        )

    if source in ("chat_promote", "new_plan", "trigger"):
        # Full decompose from intent
        intent = payload.get("intent", payload.get("description", ""))
        spec = payload.get("spec")
        quality_intent = payload.get("quality_intent")
        project_id = payload.get("project_id", "default")
        context = payload.get("context", {})
        return _decompose_from_intent(
            plan_id=plan_id,
            intent=intent,
            spec=spec,
            quality_intent=quality_intent,
            project_id=project_id,
            context=context,
            llm_call=llm_call,
            agent_configs=cfgs,
        )

    elif source == "refine":
        # Re-decompose with refinement instruction
        instruction = payload.get("instruction", "")
        intent = payload.get("intent", "")
        spec = payload.get("spec")
        quality_intent = payload.get("quality_intent")
        project_id = payload.get("project_id", "default")
        return _decompose_from_intent(
            plan_id=plan_id,
            intent=intent + f"\nRefinement: {instruction}" if instruction else intent,
            spec=spec,
            quality_intent=quality_intent,
            project_id=project_id,
            context=payload.get("context", {}),
            llm_call=llm_call,
            agent_configs=cfgs,
        )

    elif source in ("append_node", "cross_project"):
        # Incremental: add node(s) to live DAG, keep completed nodes intact
        # Convert raw dicts (from JSONB) to ChunkNode objects
        existing = [
            _parse_chunk(c) if isinstance(c, dict) else c
            for c in existing
        ]
        return _incremental_append(
            plan_id=plan_id,
            existing_chunks=existing,
            members=payload.get("members", []),
            depends_on=payload.get("depends_on", []),
            task=payload.get("task", ""),
            success_criterion=payload.get("success_criterion"),
            quality_intent=payload.get("quality_intent"),
            project_id=payload.get("project_id", "default"),
            target_project=payload.get("target_project"),
            is_cross_project=(source == "cross_project"),
            llm_call=llm_call,
        )

    else:
        raise ValueError(f"Unknown decomposition source: {source}")


def _decompose_from_intent(
    plan_id: str,
    intent: str,
    spec: str | None = None,
    quality_intent: str | None = None,
    project_id: str = "default",
    context: dict[str, Any] | None = None,
    llm_call: Callable[[str], str] | None = None,
    agent_configs: list[dict[str, Any]] | None = None,
) -> DecomposedPlan:
    """Full decomposition: break a user intent into a chunk DAG.

    Calls the brain model to propose chunks, each with >=1 member
    (specialist agent_configs) and dependency ordering.

    Memory grounding (File 08): before the LLM call, recalls product
    conventions from Neo4j and injects them into the prompt so the
    brain's DAG proposal is informed by past project patterns. After
    chunk parsing, per-node memory-grounded checks are injected via
    ``extra_checks`` into ``generate_checks()``.

    Dual-input: when ``spec`` is provided it is included in the brain
    prompt as additional constraints.  When ``quality_intent`` is provided
    it is forwarded to ``generate_checks()`` which produces additional
    checks tagged ``provenance="human_intent"``.
    """
    if llm_call is None:
        llm_call = _default_llm

    cfgs = agent_configs or []
    cfg_lines = "\n".join(
        f"  - {c.get('agent_config_id', '?')} (role: {c.get('role', '?')})"
        for c in cfgs
    )

    # ── Memory grounding: recall product conventions ────────────────────────
    memory_extra: list[str] = []
    try:
        from backend.evaluator.memory_integration import ground_checks_with_memory as _ground
        recalled = _ground(task=intent, project=project_id)
        memory_extra = [chk.criterion for chk in recalled if chk.criterion]
    except Exception:
        pass  # graceful degradation — proceed without memory context

    memory_context = ""
    if memory_extra:
        memory_context = (
            "Recalled project conventions / past error patterns:\n"
            + "\n".join(f"  - {m}" for m in memory_extra)
            + "\n\n"
        )

    spec_block = f"Spec/constraints: {spec}\n" if spec else ""

    prompt = (
        f"User intent: {intent}\n"
        f"{spec_block}"
        f"Project: {project_id}\n"
        f"Context: {json.dumps(context or {})}\n\n"
        f"{memory_context}"
        f"Available specialist agent configs:\n{cfg_lines}\n\n"
        "Break this plan into ordered chunks. Each chunk = a team led by "
        "the built-in orchestrator. You specify only the specialist members "
        "(agent_config_ids) — the orchestrator is always present implicitly.\n\n"
        "Rules:\n"
        "  - Every chunk has >=1 member (the specialist agents)\n"
        "  - The orchestrator is NOT one of the members\n"
        "  - Chunks with no dependency start first\n"
        "  - Dependent chunks wait for their dependencies to complete\n"
        "  - Typical pattern: planner -> executor -> reviewer\n\n"
        "Respond as JSON with key 'chunks' (array of objects, each with):\n"
        "  id (str, e.g. 'node-1', 'node-2'),\n"
        "  members (list of str, agent_config_ids from the list),\n"
        "  depends_on (list of str, referencing other chunk ids),\n"
        "  success (object: text=str, deterministic_checks=[str]),\n"
        "  worktree_strategy ('shared_sequential' | 'separate_parallel'),\n"
        "  commit_on_done (bool),\n"
        "  regression_required (bool),\n"
        "  retry_policy (object: max=int, backoff_s=int),\n"
        "  checks (optional array of objects with: id=str, type='deterministic'|'rubric',\n"
        "          criterion=str, check_cmd=str|null, rubric_item=str|null, weight=float)\n"
        "Also include: worktree_root (str), plan_id (str)\n"
    )

    raw = llm_call(prompt)
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        raw = raw.rsplit("```", 1)[0]

    data = json.loads(raw)
    chunks_raw = data.get("chunks", data.get("nodes", []))
    if not chunks_raw:
        raise ValueError("LLM response missing 'chunks' key")

    chunk_nodes = [_parse_chunk(c) for c in chunks_raw]
    # Generate candidate checks for any chunk without pre-populated checks
    total = len(chunk_nodes)
    for i, node in enumerate(chunk_nodes):
        per_node_extra: list = []
        try:
            for agent_id in node.members:
                recalled_node = _ground(task=node.success.text, project=project_id, agent=agent_id)
                per_node_extra.extend(recalled_node)
        except Exception:
            pass
        if not node.checks:
            generated = generate_checks(
                node_id=node.id,
                task=node.success.text,
                success_criterion=node.success.text,
                node_index=i,
                total_nodes=total,
                extra_checks=per_node_extra,
                quality_intent=quality_intent,
                members=node.members,
            )
            node.checks = generated.checks

    try:
        validate_decomposed(dplan)
    except ValueError:
        raw2 = llm_call(prompt + f"\n\nPrevious attempt failed validation. Fix.")
        raw2 = raw2.strip()
        if raw2.startswith("```"):
            raw2 = raw2.split("\n", 1)[1]
            raw2 = raw2.rsplit("```", 1)[0]
        data2 = json.loads(raw2)
        chunks_raw2 = data2.get("chunks", data2.get("nodes", chunks_raw))
        chunk_nodes2 = [_parse_chunk(c) for c in chunks_raw2]
        total2 = len(chunk_nodes2)
        for i, node in enumerate(chunk_nodes2):
            retry_extra: list = []
            try:
                for agent_id in node.members:
                    recalled_node = _ground(task=node.success.text, project=project_id, agent=agent_id)
                    retry_extra.extend(recalled_node)
            except Exception:
                pass
            if not node.checks:
                generated = generate_checks(
                    node_id=node.id,
                    task=node.success.text,
                    success_criterion=node.success.text,
                    node_index=i,
                    total_nodes=total2,
                    extra_checks=retry_extra,
                    quality_intent=quality_intent,
                    members=node.members,
                )
                node.checks = generated.checks
        dplan = DecomposedPlan(
            plan_id=data2.get("plan_id", dplan.plan_id),
            worktree_root=data2.get("worktree_root", dplan.worktree_root),
            chunks=chunk_nodes2,
        )
        validate_decomposed(dplan)

    return dplan


def _incremental_append(
    plan_id: str,
    existing_chunks: list[ChunkNode],
    members: list[str],
    depends_on: list[str],
    task: str,
    success_criterion: str | None = None,
    quality_intent: str | None = None,
    project_id: str = "default",
    target_project: str | None = None,
    is_cross_project: bool = False,
    llm_call: Callable[[str], str] | None = None,
) -> DecomposedPlan:
    """Append a single new node to an existing plan.

    Preserves all completed chunks. The new node is added to the DAG
    with the given members, dependencies, and task.
    """
    # Normalize members: extract agent_config from dict entries
    _members: list[str] = []
    for m in members:
        if isinstance(m, dict):
            _members.append(m.get("agent_config", "opencode:backend-executor"))
        else:
            _members.append(str(m))
    members = _members

    new_id = _next_node_id(existing_chunks)

    new_chunk = ChunkNode(
        id=new_id,
        members=members,
        depends_on=depends_on,
        success=NodeSuccess(text=success_criterion or task),
        commit_on_done=True,
        regression_required=True,
    )


    append_extra: list = []
    try:
        for agent_id in members:
            recalled_node = _ground(task=task, project=project_id, agent=agent_id)
            append_extra.extend(recalled_node)
    except Exception:
        pass
    generated = generate_checks(
        node_id=new_id,
        task=task,
        success_criterion=success_criterion or task,
        node_index=len(existing_chunks),
        total_nodes=len(existing_chunks) + 1,
        extra_checks=append_extra,
        quality_intent=quality_intent,
        members=members,
    )
    new_chunk.checks = generated.checks

    all_chunks = list(existing_chunks) + [new_chunk]
    return DecomposedPlan(
        plan_id=plan_id,
        worktree_root="/opt/aipc/conductor/workspace",
        chunks=all_chunks,
    )


def _parse_chunk(c: dict[str, Any]) -> ChunkNode:
    """Parse a raw chunk dict into a ChunkNode."""
    success_data = c.get("success", {})
    if isinstance(success_data, str):
        success_data = {"text": success_data, "deterministic_checks": []}

    # Support both old (ref) and new (members) formats for backwards compat
    members = c.get("members")
    if not members:
        ref = c.get("ref", "")
        if ref:
            members = [ref]
        else:
            members = ["opencode:backend-executor"]

    if isinstance(members, str):
        members = [members]
    elif members and isinstance(members[0], dict):
        members = [m.get("agent_config", "opencode:backend-executor") for m in members]

    success_text = str(success_data.get("text", ""))
    success = NodeSuccess(text=success_text)

    # Parse checks from LLM response if present, otherwise leave empty
    checks: list[Check] = []
    raw_checks = c.get("checks", [])
    if isinstance(raw_checks, list) and raw_checks:
        try:
            checks = [Check(**chk) for chk in raw_checks]
        except Exception:
            checks = []  # LLM-provided checks failed validation; skip to generation

    return ChunkNode(
        id=c["id"],
        members=members,
        depends_on=c.get("depends_on", []),
        success=success,
        checks=checks,
        worktree_strategy=c.get("worktree_strategy", "shared_sequential"),
        commit_on_done=c.get("commit_on_done", True),
        regression_required=c.get("regression_required", True),
        retry_policy=c.get("retry_policy", {"max": 2, "backoff_s": 30}),
    )


# Keep backward-compatible alias
def decompose(
    plan: Any,
    available_tools: set[str] | None = None,
    available_agent_configs: list[dict[str, Any]] | None = None,
    llm_call: Callable[[str], str] | None = None,
) -> DecomposedPlan:
    """Backward-compatible wrapper. Calls decompose_or_update with new_plan source."""
    plan_id = getattr(plan, "plan_id", str(id(plan)))
    intent = getattr(plan, "user_intent", str(plan))
    return decompose_or_update(
        plan_id=plan_id,
        source="new_plan",
        payload={"intent": intent},
        available_agent_configs=available_agent_configs,
        llm_call=llm_call,
    )
