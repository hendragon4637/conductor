import os
from pathlib import Path

from .graphiti_client import search_memory
from .scopes import group_id


def _read_project_snapshot(project):
    p = Path(os.getcwd()) / ".memory" / "snapshots" / "product_repomix.md"
    if p.exists():
        return p.read_text()
    return None


def _write_instruction_file(worktree, engine, content):
    names = {"claude": "CLAUDE.md", "gemini": "GEMINI.md", "opencode": "AGENTS.md"}
    name = names.get(engine.lower(), "AGENTS.md")
    (Path(worktree) / name).write_text(content)


async def assemble_for_node(worktree, engine, project, agent, session, task_query):
    mems = []
    for grp in [
        group_id("product"),
        group_id("product", project),
        group_id("product", project, agent),
        group_id("product", project, None, session),
    ]:
        mems += await search_memory(task_query, grp, top_k=4)
    snapshot = _read_project_snapshot(project)
    content = _render(mems, snapshot, task_query)
    _write_instruction_file(worktree, engine, content)
    return content


def _render(mems, snapshot, task_query):
    lines = []
    lines.append(f"# Memory context (assembled for: {task_query})")
    lines.append("")
    if mems:
        lines.append("## Relevant past knowledge")
        for m in mems:
            lines.append(f"- {m.get('fact', str(m))}")
        lines.append("")
    if snapshot:
        lines.append("## Project structure snapshot")
        lines.append(f"See {snapshot[:200]}...")
        lines.append("")
    return "\n".join(lines)
