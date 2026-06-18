"""Conductor built-in: build context snippets for node handoff.

After a node completes, Conductor injects the diff from completed
dependencies into the next node's brief so the team knows what changed.
"""
from __future__ import annotations

from pathlib import Path

from backend.builtins.git_ops import show_node


def build_node_context(
    worktree: str | Path,
    dep_ids: list[str],
    max_files: int = 5,
    max_lines: int = 50,
) -> str:
    """Build a handoff context string from completed dependency diffs.

    For each dependency node ID, includes the ``git show`` stat summary
    and the first *max_files* file diffs (truncated to *max_lines* per
    file).

    Returns a formatted string to inject into the orchestrator's brief.
    """
    wt = Path(worktree)
    parts: list[str] = []

    for dep_id in dep_ids:
        diff = show_node(str(wt), dep_id)
        if not diff:
            continue

        parts.append(f"=== Dependency node-{dep_id} ===")

        # Extract stat line(s) from git show output
        stat_lines: list[str] = []
        diff_lines = diff.split("\n")
        in_stat = False
        file_count = 0
        for line in diff_lines:
            if line.startswith(" ") and "|" in line and file_count < max_files:
                stat_lines.append(line.strip())
                file_count += 1
            if line.startswith("diff --git"):
                if file_count >= max_files:
                    break

        if stat_lines:
            parts.append("Changed files:")
            parts.extend(f"  {s}" for s in stat_lines)

        # Include the summary (first parent commit message + stats)
        for line in diff_lines:
            if line.startswith("    node:"):
                parts.append(f"Summary: {line.strip()}")
                break

        parts.append("")

    if not parts:
        return ""

    return "\n".join(parts).strip()
