"""Planning worktree lifecycle, scoped opencode.json, and brief generation.

Flow:
  1. ``create_planning_worktree()`` — pre-create worktree from master (or fresh)
  2. ``planning_brief()`` — build dynamic goal/spec/caps brief (static ref in NODE_BRIEF.md)
  3. ``retry_brief()`` — append file-targeted feedback for re-spawn
  4. ``on_planning_failed()`` — rm worktree after bounded attempts
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

MAX_PLANNING_ATTEMPTS = 10


# ── Worktree lifecycle ────────────────────────────────────────────────────


def create_planning_worktree(
    plan_id: str,
    project_id: str,
    workspace_root: str | Path,
) -> str:
    """Create a planning worktree from the project's master branch.

    Continuation: if the project has a master branch, the worktree is created
    from it so ``.memory/`` (which lives on master) travels into the planning
    worktree.  Fresh projects get an initialised worktree.

    Returns the absolute path to the worktree root.
    """
    root = Path(workspace_root).resolve()
    project_dir = root / project_id
    slug = re.sub(r"[^a-zA-Z0-9_.-]", "-", f"{plan_id}-planning")
    worktree_path = root / f"{project_id}.{slug}"

    if worktree_path.exists():
        logger.info("Planning worktree %s already exists — reusing", worktree_path)
        return str(worktree_path)

    # Ensure project dir exists
    if not project_dir.exists():
        project_dir.mkdir(parents=True)
        subprocess.run(
            ["git", "-C", str(project_dir), "init"],
            check=True, capture_output=True, timeout=30,
        )
        subprocess.run(
            ["git", "-C", str(project_dir), "config", "user.email",
             "conductor@aipc.local"],
            check=True, capture_output=True, timeout=30,
        )
        subprocess.run(
            ["git", "-C", str(project_dir), "config", "user.name", "Conductor"],
            check=True, capture_output=True, timeout=30,
        )
        readme = project_dir / "README.md"
        readme.write_text(f"# {project_id}\n")
        subprocess.run(
            ["git", "-C", str(project_dir), "add", "."],
            check=True, capture_output=True, timeout=30,
        )
        subprocess.run(
            ["git", "-C", str(project_dir), "commit", "-m", "init"],
            check=True, capture_output=True, timeout=30,
        )

    # Remove prior worktree if exists
    subprocess.run(
        ["git", "-C", str(project_dir), "worktree", "remove", "--force", str(worktree_path)],
        capture_output=True, timeout=60,
    )

    # Create worktree from master (or current branch)
    subprocess.run(
        ["git", "-C", str(project_dir), "branch", "-D", f"planning-{plan_id}"],
        capture_output=True, timeout=30,
    )
    subprocess.run(
        ["git", "-C", str(project_dir), "worktree", "add",
         "-b", f"planning-{plan_id}", str(worktree_path)],
        check=True, capture_output=True, timeout=60,
    )

    # Scaffold .plan/ dirs
    (worktree_path / ".plan" / "nodes").mkdir(parents=True, exist_ok=True)
    (worktree_path / ".plan" / "checks").mkdir(parents=True, exist_ok=True)
    (worktree_path / "plan_scratch").mkdir(exist_ok=True)

    # Write scoped opencode.json
    _write_planner_opencode_json(worktree_path)

    logger.info("Planning worktree created at %s", worktree_path)
    return str(worktree_path)


def on_planning_failed(
    worktree_path: str,
    project_id: str,
    workspace_root: str | Path,
) -> None:
    """Remove planning worktree after final failure. Idempotent."""
    root = Path(workspace_root).resolve()
    project_dir = root / project_id
    wt = Path(worktree_path)

    if not wt.exists():
        logger.info("Planning worktree %s already removed", worktree_path)
        return

    subprocess.run(
        ["git", "-C", str(project_dir), "worktree", "remove", "--force", str(wt)],
        capture_output=True, timeout=60,
    )
    # Also remove the branch
    subprocess.run(
        ["git", "-C", str(project_dir), "branch", "-D", f"planning-{wt.name}"],
        capture_output=True, timeout=30,
    )
    logger.info("Planning worktree removed %s", worktree_path)


# ── Scoped opencode.json ──────────────────────────────────────────────────


def _write_planner_opencode_json(worktree: Path) -> None:
    """Write scoped permissions to the planning worktree.

    The meta-planner agent may edit ONLY ``.plan/**`` and ``plan_scratch/**``.
    Bash is read-only (ls, cat, find). Research (webfetch, websearch) allowed.

    Reads the meta-planner agent config from the database to populate the
    model, allowed tools, and agent definition in opencode.json, so the
    worktree reflects the full agent profile (not just hardcoded permissions).
    """
    from backend.db.queries import get_agent_config

    # The NODE_BRIEF.md is loaded via instructions so the brief is visible to the agent.
    # It carries the static reference content (role, steps, rules, schemas, roster)
    # plus the DB system_prompt. The dynamic brief is sent as a separate message.
    cfg = get_agent_config("meta-planner") or {}
    model = cfg.get("model_preference") or "litellm/deepseek-planning"
    sys_prompt = (cfg.get("system_prompt") or "").strip()

    conductor_dir = worktree / ".conductor"
    conductor_dir.mkdir(parents=True, exist_ok=True)
    brief_path = conductor_dir / "NODE_BRIEF.md"

    static_brief = _build_static_brief()
    brief_content = static_brief
    if sys_prompt:
        brief_content = f"{sys_prompt}\n\n---\n\n{static_brief}"
    brief_path.write_text(brief_content, encoding="utf-8")

    config: dict[str, Any] = {
        "$schema": "https://opencode.ai/config.json",
        "model": model,
        "permission": {
            "edit": "allow",
            "bash": "allow",
        },
    }
    if sys_prompt:
        config["instructions"] = ["{file:./.conductor/NODE_BRIEF.md}"]

    (worktree / "opencode.json").write_text(
        json.dumps(config, indent=2) + "\n",
    )
    logger.debug("Scoped opencode.json written to %s", worktree)


# ── Slate helpers (DB queries) ────────────────────────────────────────────


def _roster_slate() -> list[dict[str, Any]]:
    """Return all active agent_configs with their capabilities, for the brief.

    Each entry: ``{"agent_config_id": "...", "capabilities": [...], "backend": "..."}``
    """
    from backend.db.queries import conn as db_conn

    with db_conn() as c:
        rows = c.execute(
            """
            SELECT agent_config_id, COALESCE(new_capabilities, '[]'::jsonb) AS caps,
                   COALESCE(execution->>'backend', 'opencode') AS backend
            FROM agent_configs
            WHERE active = true
            ORDER BY agent_config_id
            """
        ).fetchall()
    return [
        {"agent_config_id": r["agent_config_id"], "capabilities": r["caps"] or [],
         "backend": r["backend"] or "opencode"}
        for r in rows
    ]


def _capability_slate(domain: str | None = None) -> list[dict[str, Any]]:
    from backend.db.queries import conn as db_conn

    if domain:
        from backend.planning.capability.selector import DOMAIN_TO_FAMILY
        families = DOMAIN_TO_FAMILY.get(domain, [])
        if families:
            with db_conn() as c:
                placeholders = ",".join("%s" for _ in families)
                rows = c.execute(
                    f"""
                    SELECT name, family
                    FROM capabilities
                    WHERE family ?| array[{placeholders}]
                    ORDER BY name
                    """,
                    families,
                ).fetchall()
            return [
                {"name": r["name"], "family": r["family"]}
                for r in rows
            ]

    with db_conn() as c:
        rows = c.execute(
            """
            SELECT name, family
            FROM capabilities
            ORDER BY name
            """
        ).fetchall()
    return [
        {"name": r["name"], "family": r["family"]}
        for r in rows
    ]


def capability_dims_slate(
    domain: str | None = None,
) -> list[dict[str, Any]]:
    """Return capabilities with their quality_dimensions for the brief.

    Same family-filter as ``_capability_slate`` but includes the
    ``quality_dimensions`` field so the meta-planner agent can seed
    per-node checks from them.
    """
    from backend.db.queries import conn as db_conn

    families: list[str] = []
    if domain:
        from backend.planning.capability.selector import DOMAIN_TO_FAMILY
        families = DOMAIN_TO_FAMILY.get(domain, [])

    if families:
        with db_conn() as c:
            placeholders = ",".join("%s" for _ in families)
            rows = c.execute(
                f"""
                SELECT name, family, quality_dimensions
                FROM capabilities
                WHERE family ?| array[{placeholders}]
                ORDER BY name
                """,
                families,
            ).fetchall()
        return [
            {
                "name": r["name"],
                "family": r["family"],
                "dimensions": r.get("quality_dimensions") or [],
            }
            for r in rows
        ]

    with db_conn() as c:
        rows = c.execute(
            """
            SELECT name, family, quality_dimensions
            FROM capabilities
            ORDER BY name
            """
        ).fetchall()
    return [
        {
            "name": r["name"],
            "family": r["family"],
            "dimensions": r.get("quality_dimensions") or [],
        }
        for r in rows
    ]


def _schema_text() -> str:
    """Return JSON schema docstrings for the brief."""
    from contracts.plan_assembler import (
        check_json_schema,
        index_json_schema,
        per_node_json_schema,
    )

    idx_schema = index_json_schema()
    node_schema = per_node_json_schema()
    ck_schema = check_json_schema()
    return (
        f"INDEX SCHEMA (for .plan/index.json):\n{json.dumps(idx_schema, indent=2)}\n\n"
        f"NODE SCHEMA (for each .plan/nodes/node-NNN.json):\n{json.dumps(node_schema, indent=2)}\n\n"
        f"CHECK SCHEMA (for each entry in .plan/checks/node-NNN.json):\n{json.dumps(ck_schema, indent=2)}"
    )


def _build_static_brief() -> str:
    """Build the static reference section for NODE_BRIEF.md.

    This content does NOT depend on the specific goal — it is the meta-planner's
    reference manual (role, steps, rules, schemas, roster). Written once at
    worktree creation and loaded as an ``{file:}`` instruction so the agent sees
    it alongside the dynamic ``planning_brief()`` message.
    """
    parts: list[str] = []
    _NL = "\n"

    parts.append("# META-PLANNER REFERENCE")
    parts.append("")
    parts.append("## YOUR ROLE")
    parts.append("")
    parts.append(
        "You are a **Plan Architect** — an expert agent that decomposes goals "
        "into structured, executable plans. Your job is to take a goal and spec, "
        "examine the available capabilities (agents + tools), and produce a DAG "
        "of work nodes that, together, achieve the goal. Each node specifies what "
        "capability (agent type) will do the work, what files it may touch, and "
        "what quality gates (checks) must pass."
    )
    parts.append("")

    parts.append("## EXECUTION STEPS")
    parts.append("")
    parts.append("Follow these steps **in order** to produce the plan DAG.")
    parts.append("")
    steps = [
        (
            "### STEP 1 — Formulate",
            "Read the goal and spec below. Clarify ambiguities with the user if needed. "
            "If you cannot produce a confident decomposition, ask clarifying questions.",
        ),
        (
            "### STEP 2 — Scope",
            "Identify the concrete files, APIs, or components that need to be created "
            "or modified. Determine what is in scope and out of scope.",
        ),
        (
            "### STEP 3 — Assign Capabilities",
            "Examine the Roster below. For each work chunk, choose the most suitable "
            "capability (highest tool/verification match). Assign exactly one capability per node.",
        ),
        (
            "### STEP 4 — Generate Plan DAG",
            "Create JSON files at the worktree path following the schema below. "
            "Write .plan/index.json first, then per-node files, then per-node check files.",
        ),
    ]
    for title, desc in steps:
        parts.append(title)
        parts.append(desc)
        parts.append("")

    parts.append("## OUTPUT FORMAT (STRICT — FOLLOW THIS PROCEDURE)")
    parts.append("")
    parts.append(
        "**STEP 1** — Write .plan/index.json ONLY first (skeleton): "
        "an index with goal, spec, quality_intent, and a nodes array listing "
        "each node's id, file, depends_on, and description."
    )
    parts.append("")
    parts.append(
        "**STEP 2** — For each node in index order, write .plan/nodes/node-NNN.json "
        "(filename MUST match id). Each node file carries full fields: "
        "deliverables, members, edit paths, context paths, and the capabilities list."
    )
    parts.append("")
    parts.append(
        "**STEP 3** — For each node, write .plan/checks/node-NNN.json seeded from "
        "that node's capability quality_dimensions. "
        "Objective dimensions → L1 deterministic checks (with a ``cmd``). "
        "Subjective dimensions → L2 rubric checks. "
        "EVERY node MUST include the ``run_md_present`` L1 check "
        "(id='run_md_present', cmd='test -f RUN.md'). "
        "Do NOT include runtime checks (curl, localhost, pytest) on non-runnable nodes."
    )
    parts.append("")
    parts.append(
        "**STEP 4** — SELF-VERIFY before finishing: "
        "Re-read .plan/index.json and confirm every listed node has BOTH its "
        ".plan/nodes/ file AND its .plan/checks/ file on disk. "
        "Verify every node's internal ``id`` field matches its filename. "
        "Verify no orphan files exist. "
        "Fix ANY mismatch found. Only when everything is consistent, respond "
        "with a short confirmation."
    )
    parts.append("")

    parts.append("## RULES")
    parts.append("")
    rules = [
        "Nodes must be scoped and right-sized (not too coarse, not too fine).",
        "Dependencies must be acyclic and resolve within the DAG.",
        "Every node must have at least one deliverable.",
        "Members MUST be from the ROSTER only — never hallucinate an agent_config.",
        "Each node's ``capabilities`` list MUST be a subset of the assigned member's "
        "declared capabilities (shown in the ROSTER).",
        "Write ONLY .plan/ and plan_scratch/ files. Do NOT write code or touch other files.",
        "You MUST use the ``write`` tool — do NOT output file contents in your message.",
    ]
    for rule in rules:
        parts.append(f"- {rule}")
    parts.append("")

    schema = _schema_text()
    parts.append("## PLAN DAG SCHEMA")
    parts.append("")
    parts.append(schema)
    parts.append("")

    roster = _roster_slate()
    parts.append("## AVAILABLE CAPABILITIES (Roster)")
    parts.append("")
    parts.append(
        "The following agent configurations are available. "
        "Each has a unique ``agent_config_id`` and a list of ``capabilities``. "
        "Assign members from this list only."
    )
    parts.append("")
    parts.append(json.dumps(roster, indent=2))
    parts.append("")

    return _NL.join(parts)


# ── Brief builders ────────────────────────────────────────────────────────


def planning_brief(
    meta_goal: dict[str, Any],
    worktree: str,
) -> str:
    """Build the dynamic portion of the meta-planner brief.

    The static reference (role, steps, rules, schemas, roster) lives in
    NODE_BRIEF.md loaded as a ``{file:}`` instruction. This message provides
    only the dynamic parts: goal, spec, quality_intent, worktree path, and
    domain-filtered capabilities/dimensions.

    The full brief the agent sees = NODE_BRIEF.md (instruction) + this message.
    """
    caps = _capability_slate(meta_goal.get("domain"))
    dims = capability_dims_slate(meta_goal.get("domain"))

    caps_formatted = "\n".join(
        f"  - {c['name']}  (family={c['family']})"
        for c in caps
    )
    dims_formatted = "\n".join(
        f"  - {d['name']}  dims={d['dimensions']}"
        for d in dims
    )

    return f"""GOAL: {meta_goal.get('goal', '')}

SPEC: {meta_goal.get('spec', '')}

QUALITY INTENT (guide for plan structure, NOT implementation details):
{meta_goal.get('quality_intent', '')}
    The quality intent describes what makes a good PLAN: appropriate node scope,
    well-defined checks, clear deliverables, and realistic dependencies.
    It is NOT a specification for code implementation.

WORKTREE: {worktree}

CAPABILITY VOCABULARY (use these capability names in the ``capabilities`` field of each node):
{caps_formatted}

CAPABILITY DIMENSIONS (seed checks from these quality_dimensions — objective → L1, subjective → L2):
{dims_formatted}

See .conductor/NODE_BRIEF.md for the full static reference (schemas, detailed instructions, rules, and capability roster)."""


def retry_brief(
    prior_feedback: list[str],
    meta_goal: dict[str, Any],
    worktree: str,
) -> str:
    """Build a retry brief with structured ✓/FIX block leading.

    Order: ✓/FIX block (deterministic file check) → raw errors → fix instructions.
    The ✓/FIX block tells the agent what files are correct (do NOT touch)
    and what specifically needs fixing (with exact action verbs).

    Args:
        prior_feedback: List of error messages from the assembler, pydantic,
            or gate (file-targeted when possible).
        meta_goal: Same meta-goal dict as the original brief.
        worktree: Path to the planning worktree.

    Returns:
        Full brief string with ✓/FIX block leading, then errors.
    """
    from contracts.plan_assembler import render_deterministic_feedback

    base = planning_brief(meta_goal, worktree)
    fix_block = render_deterministic_feedback(worktree)
    feedback_block = "\n".join(f"  - {msg}" for msg in prior_feedback)
    return f"""{base}

# ── DETERMINISTIC VALIDATION RESULT ────────────────────────
{fix_block}

# ── RAW ERRORS FROM ASSEMBLER / GATE ──────────────────────
{"\n".join(f"  - {msg}" for msg in prior_feedback) if prior_feedback else "  (no raw errors)"}

# ── INSTRUCTIONS ───────────────────────────────────────────
Fix ONLY the files listed under "FIX THESE" above. Do NOT touch files listed under "CORRECT".
Edit the specific .plan/ files referenced — do NOT rewrite the entire plan from scratch.
"""
