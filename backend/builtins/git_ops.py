"""Conductor built-in: deterministic git operations for node lifecycle.

Conductor calls these after node completion (commit/ tag) and before
the next node (diff injection). No agent ever runs git — Conductor
handles it deterministically.
"""
from __future__ import annotations

import subprocess
from pathlib import Path


def commit_node(worktree: str | Path, node_id: str, summary: str = "") -> str:
    """Commit all changes in the worktree and create a ``node-<id>`` tag.

    Returns the tag name.
    """
    wt = Path(worktree)
    tag = f"node-{node_id}"

    _git(wt, "add", "-A")
    msg = f"node:{node_id} {summary}".strip() if summary else f"node:{node_id}"
    _git(wt, "commit", "--allow-empty", "-m", msg)
    # Remove existing tag if present (re-run safe)
    _git(wt, "tag", "-f", tag)
    return tag


def show_node(worktree: str | Path, node_id: str) -> str:
    """Return the ``git show`` output (stat + diff) for a node tag.

    Returns empty string if the tag does not exist.
    """
    wt = Path(worktree)
    tag = f"node-{node_id}"
    try:
        return _git(wt, "show", tag, "--stat", "--format=fuller")
    except RuntimeError:
        return ""


def reset_to(worktree: str | Path, node_id: str) -> None:
    """Hard-reset the worktree to a node tag (for retry/revert)."""
    wt = Path(worktree)
    tag = f"node-{node_id}"
    _git(wt, "reset", "--hard", tag)


def _git(worktree: Path, *args: str) -> str:
    """Run a git command in the worktree directory."""
    result = subprocess.run(
        ["git", *args],
        cwd=str(worktree),
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed in {worktree}: {result.stderr.strip()}"
        )
    return result.stdout.strip()
