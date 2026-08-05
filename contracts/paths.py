"""Infrastructure path exclusions shared across watcher, executor, evaluator.

Infra files (skills, briefs, venv, plan, memory, caches) must never appear in:
- ``fs_changed`` detection (watcher)
- Dependency-context diffs (handoff / brief builder)
- Artifact bundles sent to the L2 judge (evaluator)
- Git commits from worktrees

A single constant lives here; every consumer imports from this module.
"""

from __future__ import annotations

from pathlib import Path

# ── Paths that are NOT product work and must be excluded everywhere ──────────

INFRA_EXCLUDES: list[str] = [
    ".opencode/",
    ".conductor/",
    ".plan/",
    ".memory/",
    ".venv/",
    "node_modules/",
    "__pycache__/",
    ".pytest_cache/",
    ".mypy_cache/",
    ".ruff_cache/",
    "l4_scratch/",
    "opencode.json",
    "*.egg-info/",
    "deps/",
]

# Also used by the evaluator artifact bundle
INFRA_SKIP_PARTS: set[str] = {
    ".opencode",
    ".conductor",
    ".plan",
    ".memory",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "l4_scratch",
    "deps",
}

# Read-only context paths (deps/ + .conductor/references/) that nodes must
# never modify or depend on. Shared by plan evaluator Check 7 and the L2 gate
# feedback filter so both guard the same set.
READ_ONLY_CONTEXT_PATHS: tuple[str, ...] = (
    "deps/",
    ".conductor/references/",
)


def is_infra(path: str) -> bool:
    """Return True if *path* matches an infra exclusion pattern.

    Matches both a leading prefix (e.g. ``.venv/`` at the start)
    and ``/<dir>/`` anywhere in the path (e.g. ``some/deep/.venv/pkg``).
    """
    for prefix in INFRA_EXCLUDES:
        if path.startswith(prefix) or f"/{prefix}" in path:
            return True
    return False


def git_pathspec_excludes() -> list[str]:
    """Return ``:(exclude)`` pathspecs for every infra path.

    Pass these to ``git status --porcelain -- . <excludes>`` or
    ``git diff -- . <excludes>`` to skip infra-only changes.
    """
    return [f":(exclude){p}" for p in INFRA_EXCLUDES]


def worktree_gitignore_lines() -> str:
    """Content for the worktree ``.gitignore`` file.

    These paths are never product work and should never be committed:
      - ``.opencode/``, ``.conductor/``, ``.plan/`` — conductor scaffolding
      - ``.venv/``, ``node_modules/`` — language runtimes
      - ``__pycache__/``, ``*.pyc`` — bytecode
      - ``l4_scratch/`` — scratch dirs

    NOTE: ``.memory/`` is deliberately NOT gitignored — it must travel
    with the repo as versioned project knowledge.
    """
    return "\n".join(sorted(INFRA_EXCLUDES)) + "\n"
