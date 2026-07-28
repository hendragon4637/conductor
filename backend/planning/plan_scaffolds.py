"""Deterministic .plan/ scaffold injector for planning worktrees.

Written at worktree creation time to give the meta-planner a pre-populated
structure: index.json skeleton, node stubs, check stubs, and a TODO.md
checklist.  Idempotent — skips files that already exist so retry/steering
does not clobber the meta-planner's progress.

No domain-specific logic; the node count comes from the meta-goal's
``estimated_node_count`` field (set by the formulate LLM, hard-capped at 5).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _index_skeleton(
    goal: str,
    spec: str,
    quality_intent: str,
    node_count: int,
) -> dict[str, Any]:
    """Build a pre-populated ``.plan/index.json``."""
    nodes: list[dict[str, Any]] = []
    for i in range(1, node_count + 1):
        nodes.append({
            "id": f"node-{i:03d}",
            "file": f"node-{i:03d}.json",
            "depends_on": [f"node-{i-1:03d}"] if i > 1 else [],
            "description": "",
        })
    return {
        "goal": goal or "",
        "spec": spec or "",
        "quality_intent": quality_intent or "",
        "nodes": nodes,
    }


def _node_stub(node_id: str) -> dict[str, Any]:
    """Build a stub ``.plan/nodes/{node_id}.json``."""
    return {
        "id": node_id,
        "capabilities": [],
        "members": [],
        "depends_on": [],
        "task": {"text": "", "inputs": [], "deliverables": []},
        "success": {"text": ""},
    }


def _check_stub() -> list[dict[str, Any]]:
    """Build a stub ``.plan/checks/{node_id}.json`` with mandatory L1 check."""
    return [
        {
            "id": "run_md_present",
            "tier": "L1",
            "kind": "deterministic",
            "cmd": "test -f RUN.md",
            "expect": {"exit_code": 0},
            "criterion": "RUN.md exists in the worktree root",
        },
    ]


def _todo_content(node_count: int) -> str:
    """Build ``.plan/TODO.md`` checklist."""
    lines = [
        "# Plan Checklist",
        "",
        "## Phase 1 — Index",
        "- [ ] Fill .plan/index.json goal",
        "- [ ] Fill .plan/index.json spec",
        "- [ ] Fill .plan/index.json quality_intent",
        "- [ ] Set dependencies in .plan/index.json nodes array",
        "",
        "## Phase 2 — Node files",
    ]
    for i in range(1, node_count + 1):
        lines.append(f"- [ ] Write .plan/nodes/node-{i:03d}.json")
    lines += [
        "",
        "## Phase 3 — Check files",
    ]
    for i in range(1, node_count + 1):
        lines.append(f"- [ ] Write .plan/checks/node-{i:03d}.json")
    lines += [
        "",
        "## Phase 4 — Self-verify",
        "- [ ] Every node in index.json has a matching .plan/nodes/ file",
        "- [ ] Every node has a matching .plan/checks/ file",
        "- [ ] Node internal id matches filename",
        "- [ ] No orphan files in .plan/nodes/ or .plan/checks/",
        "",
    ]
    return "\n".join(lines)


def scaffold_plan_worktree(
    worktree: str | Path,
    meta_goal: dict[str, Any] | None = None,
    node_count: int | None = None,
) -> None:
    """Write deterministic .plan/ scaffold files into *worktree*.

    Called by ``create_planning_worktree()`` after creating the empty dirs.
    Idempotent — every write is guarded by ``if not path.exists()`` so the
    meta-planner's output survives retry/steering cycles.

    Args:
        worktree: Absolute path to the planning worktree root.
        meta_goal: The MetaGoal dict (used for goal/spec/quality_intent).
        node_count: Number of nodes to scaffold.  If ``None``, read from
            ``meta_goal.get("estimated_node_count", 2)``.
    """
    wt = Path(worktree)

    if node_count is None:
        node_count = (meta_goal or {}).get("estimated_node_count", 2) or 2
    nc = node_count if node_count is not None else 2
    node_count = max(1, min(nc, 5))  # hard cap at 5

    goal = (meta_goal or {}).get("goal", "")
    spec = (meta_goal or {}).get("spec", "")
    quality_intent = (meta_goal or {}).get("quality_intent", "")

    # ── .plan/index.json skeleton ──────────────────────────────────────
    idx_path = wt / ".plan" / "index.json"
    if not idx_path.exists():
        idx_data = _index_skeleton(goal, spec, quality_intent, node_count)
        idx_path.write_text(json.dumps(idx_data, indent=2) + "\n")
        logger.debug("Scaffolded %s (%d nodes)", idx_path, node_count)

    # ── .plan/nodes/ stubs ────────────────────────────────────────────
    nodes_dir = wt / ".plan" / "nodes"
    for i in range(1, node_count + 1):
        nid = f"node-{i:03d}"
        nf = nodes_dir / f"{nid}.json"
        if not nf.exists():
            stub = _node_stub(nid)
            nf.write_text(json.dumps(stub, indent=2) + "\n")
            logger.debug("Scaffolded %s", nf)

    # ── .plan/checks/ stubs ───────────────────────────────────────────
    checks_dir = wt / ".plan" / "checks"
    for i in range(1, node_count + 1):
        nid = f"node-{i:03d}"
        cf = checks_dir / f"{nid}.json"
        if not cf.exists():
            stub = _check_stub()
            cf.write_text(json.dumps(stub, indent=2) + "\n")
            logger.debug("Scaffolded %s", cf)

    # ── .plan/TODO.md ─────────────────────────────────────────────────
    todo_path = wt / ".plan" / "TODO.md"
    if not todo_path.exists():
        todo_path.write_text(_todo_content(node_count))
        logger.debug("Scaffolded %s", todo_path)

    logger.info(
        "Planning scaffold injected: domain-agnostic, %d nodes, TODO.md created",
        node_count,
    )
