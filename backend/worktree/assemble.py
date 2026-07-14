"""Assemble memory + config into a freshly created worktree.

Called just before spawning an AionUi session so the agent has all context
it needs (permission rules, instructions, skills).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any
import psycopg
from psycopg.rows import dict_row

from contracts.paths import worktree_gitignore_lines
from backend.adapters.registry import get_adapter


def assemble_for_spawn(
    worktree: Path,
    cli: str,
    agent_config: dict[str, Any],
    project_id: str,
    session_id: str,
    db_url: str | None = None,
    auto_approve: bool = True,
    permission_rules: dict[str, Any] | None = None,
) -> None:
    """Write permission, instructions, and skills into the worktree.

    Args:
        worktree: Absolute path to the session worktree.
        cli: Engine name (e.g. ``"opencode"``) used to pick the adapter.
        agent_config: Full row from agent_configs (must include
            ``permission_policy``, ``system_prompt``, ``skill_path``).
        project_id: Project identifier.
        session_id: Session identifier.
        db_url: Postgres connection string.  Falls back to ``DATABASE_URL``
            env var if omitted.
        auto_approve: When True the adapter writes an all-allow opencode.json
            so the agent never prompts.  When False the per-project config
            is skipped and OpenCode falls back to its global config.
    """
    adapter = get_adapter(cli)
    _db_url = db_url or os.environ.get("DATABASE_URL", "")

    # 1. Permission (deterministic)
    if auto_approve:
        policy = {"mode": "auto_approve"}
        perm_config = {"edit": "allow", "bash": "allow", "webfetch": "allow"}
    else:
        policy = dict(permission_rules or agent_config.get("permission_policy") or {})
        perm_config = dict(policy)
    adapter.write_permission(worktree, policy)

    if cli.startswith("opencode"):
        from backend.backends.opencode_config import write_worktree_config

        model = agent_config.get("model_preference") or "litellm/gptoss-exec"
        agent_config_id = agent_config.get("agent_config_id", "")
        agent_name = agent_config_id.split(":", 1)[-1] if ":" in agent_config_id else agent_config_id
        sys_prompt = agent_config.get("system_prompt", "")

        write_worktree_config(
            worktree=worktree,
            model=model,
            permissions=perm_config,
            appended_prompt=sys_prompt or None,
            agent_name=agent_name,
            agent_type=cli,
        )

        # 1b. .gitignore — infra paths never committed
        gitignore_path = worktree / ".gitignore"
        if not gitignore_path.exists():
            gitignore_path.write_text(worktree_gitignore_lines())

    # 2. Instructions = global + project + session memory, concatenated
    parts: list[str] = []

    if _db_url:
        memory_rows = _load_memory(_db_url, project_id, session_id)
        for row in memory_rows:
            body = _read_memory_file(row["file_path"])
            if body:
                title = row.get("title", "")
                parts.append(f"## {title}\n\n{body}")

    workspace_conventions = """
## Workspace Conventions

### Root-level project layout
- The workspace root IS your project root. All scaffolding files (pyproject.toml, RUN.md, .venv/) go at the root.
- Do NOT create a wrapper project folder (e.g., `my_project/`, `project/`, `app/`) — files and directories go directly under the root, not nested inside a wrapper.
- Standard source subdirectories (e.g., `app/`, `src/`, `tests/`) are fine — create them when the task's deliverable paths require them. The rule is: no WRAPPER folder, but yes SOURCE folders as specified.

### Virtual environment
- Use `uv venv .venv` to create the virtual environment at the workspace root.
- Use `uv pip install` to install dependencies.
- `uv` is available globally at `/home/aipc/.local/bin/uv`.
- Do NOT use `python3 -m venv` (requires python3-venv package, not installed).
- Do NOT use `pip install` at system level (blocked by PEP 668 externally-managed-environment).

### Scaffolding files (always at root)
- `pyproject.toml` — at the workspace root.
- `RUN.md` — at the workspace root.
- `.venv/` — at the workspace root.

### Judge feedback is authoritative
- The evaluator judge's feedback (L1 check failures and L2 rubric scores) is the ground truth.
- If the judge says a file is missing, wrong, or a check failed — fix it. Do NOT argue, rationalize, or explain why your approach is also valid.
- Even if you believe your implementation is correct, the judge's criteria define success. Adjust to match.
- Read the failed check output carefully, identify what file or content is expected, and produce exactly that.
- Treat each remediation attempt as a fresh chance to comply with the feedback — iterate toward what the judge asks for, not what you think is right.
""".strip()

    parts.append(workspace_conventions)

    # 3. Agent config system prompt
    sys_prompt = agent_config.get("system_prompt", "")
    if sys_prompt:
        parts.append(sys_prompt)

    adapter.write_instructions(worktree, "\n\n---\n\n".join(p for p in parts if p))

    # 4. Skills (probabilistic)
    skill_path = agent_config.get("skill_path")
    skills: dict[str, str] = {}
    if skill_path:
        sp = Path(skill_path)
        if sp.is_file():
            skill_name = sp.parent.name  # e.g. "executor" from ".../executor/SKILL.md"
            skills[skill_name] = sp.read_text()
    adapter.write_skills(worktree, skills)

    # 5. Bump use_count on memory rows used
    if _db_url:
        _bump_memory_usage(_db_url, project_id, session_id)


def _load_memory(db_url: str, project_id: str, session_id: str) -> list[dict]:
    """Fetch active memory rows ordered least-specific first."""
    with psycopg.connect(db_url, row_factory=dict_row) as c:
        with c.cursor() as cur:
            cur.execute(
                """SELECT memory_id, scope, file_path, title
                   FROM agent_memory
                   WHERE active
                     AND (
                       scope = 'global'
                       OR (scope = 'project' AND project_id = %s)
                       OR (scope = 'session' AND project_id = %s AND session_id = %s)
                     )
                   ORDER BY
                     CASE scope
                       WHEN 'global' THEN 0
                       WHEN 'project' THEN 1
                       WHEN 'session' THEN 2
                       ELSE 3
                     END,
                     title
                """,
                (project_id, project_id, session_id),
            )
            return cur.fetchall()


def _read_memory_file(file_path: str) -> str:
    """Read a memory markdown file from disk."""
    try:
        p = Path(file_path)
        if p.is_file():
            return p.read_text()
        return ""
    except (OSError, PermissionError):
        return ""


def _bump_memory_usage(db_url: str, project_id: str, session_id: str) -> None:
    """Increment use_count for memory rows referenced in this session."""
    with psycopg.connect(db_url) as c:
        with c.cursor() as cur:
            # agent_memory doesn't have use_count yet — this is a no-op
            # placeholder. File 10 (ratchet) adds the column if needed.
            pass
