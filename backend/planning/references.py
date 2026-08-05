"""Per-project references store — context material copied into worktrees.

Store layout::

    <REFERENCES_ROOT>/<project_id>/README.md   (required — validity gate)
    <REFERENCES_ROOT>/<project_id>/...         (any context files)

At spawn (planning + execution) the project's reference dir is copied into
``.conductor/references/`` in the worktree.  It is gitignored (never
committed), read-only for agents, and excluded from evaluator artifacts
via ``.conductor/`` in ``INFRA_EXCLUDES``.

The planning brief points the planner at ``.conductor/references/*/README.md``
when references exist.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

REFERENCES_ROOT = Path(
    os.environ.get("REFERENCES_ROOT", "/opt/aipc/conductor/references")
)

# References are human-maintained context, not product work — copied verbatim.
# ``.git`` is never copied (a copied repo is context, not a git history).
_COPY_IGNORE = shutil.ignore_patterns(".git")


def project_references_dir(project_id: str) -> Path:
    """Absolute path to a project's reference dir in the store."""
    return REFERENCES_ROOT / project_id


def has_references(project_id: str) -> bool:
    """True when the project has a valid reference (dir + README.md)."""
    ref_dir = project_references_dir(project_id)
    return ref_dir.is_dir() and (ref_dir / "README.md").is_file()


def copy_references(project_id: str, target_worktree: str | Path) -> Path | None:
    """Copy ``<REFERENCES_ROOT>/<project_id>/`` into ``<worktree>/.conductor/references/``.

    Returns the copied directory, or ``None`` when the project has no valid
    reference (missing dir or missing ``README.md`` — logged, never fatal).
    """
    src = project_references_dir(project_id)
    if not src.is_dir():
        return None
    if not (src / "README.md").is_file():
        logger.warning(
            "References dir %s has no README.md — skipping copy", src
        )
        return None

    dst = Path(target_worktree) / ".conductor" / "references"
    _ = shutil.copytree(src, dst, dirs_exist_ok=True, ignore=_COPY_IGNORE)
    logger.info("Copied references for %s -> %s", project_id, dst)
    return dst


def references_in_worktree(target_worktree: str | Path) -> Path:
    """Path to the references dir inside a worktree (may not exist)."""
    return Path(target_worktree) / ".conductor" / "references"


def gitignore_references(target_worktree: str | Path) -> None:
    """Ensure ``.conductor/references/`` stays gitignored in *target_worktree*.

    Planning worktrees un-ignore ``.conductor/`` so agent tools can see
    NODE_BRIEF.md; this re-ignores only the references subdir so reference
    files are never committed.
    """
    gi = Path(target_worktree) / ".gitignore"
    line = ".conductor/references/"
    existing = gi.read_text().splitlines() if gi.exists() else []
    if line not in existing:
        _ = gi.write_text("\n".join(existing + [line]) + "\n", encoding="utf-8")
