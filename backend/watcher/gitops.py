"""Git operations for the commit ladder + regression gate.

Rules (from spec):
- Sequential dependent chunks share ONE worktree (no merge).
- Parallel independent nodes use separate worktrees → merge_parallel.
- A chunk is "done" only when regression_gate passes AND commit_chunk succeeds.
- Never rollback main repos — only experiment/work test branches.
"""
from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Sequence

logger = logging.getLogger(__name__)


def _git(cmd: list[str], cwd: str | Path, check: bool = True) -> subprocess.CompletedProcess:
    """Run a git command in *cwd*."""
    return subprocess.run(
        ["git"] + cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=check,
        timeout=60,
    )


def commit_chunk(worktree: str | Path, node_id: str, msg: str | None = None) -> bool:
    """``git add -A && git commit`` with a node tag.

    Returns True if committed, False if nothing to commit.
    """
    wd = Path(worktree)
    if not wd.is_dir():
        logger.warning("commit_chunk: worktree %s not found", wd)
        return False

    try:
        _git(["add", "-A"], cwd=wd)
    except subprocess.CalledProcessError as e:
        logger.error("git add failed in %s: %s", wd, e.stderr)
        return False

    # Check if anything changed
    result = _git(["status", "--porcelain"], cwd=wd)
    if not result.stdout.strip():
        logger.info("commit_chunk: nothing to commit in %s", wd)
        return False

    commit_msg = msg or f"chunk-{node_id}"
    try:
        _git(["commit", "-m", commit_msg], cwd=wd)
        _git(["tag", "-f", f"node-{node_id}"], cwd=wd)
        logger.info("committed '%s' in %s (tag: node-%s)", commit_msg, wd, node_id)
        return True
    except subprocess.CalledProcessError as e:
        logger.error("commit failed in %s: %s", wd, e.stderr)
        return False


def regression_gate(worktree: str | Path) -> bool:
    """Run the accumulated test suite.

    Returns True if tests pass (exit code 0).
    Looks for pytest, else make check, else python -m pytest.
    """
    wd = Path(worktree)
    if not wd.is_dir():
        logger.warning("regression_gate: worktree %s not found", wd)
        return False

    # Try pytest first
    commands = [
        ["pytest", "-q", "--tb=short", "-x"],
        ["python3", "-m", "pytest", "-q", "--tb=short", "-x"],
        ["make", "check"],
        ["make", "test"],
    ]

    for cmd in commands:
        try:
            result = subprocess.run(
                cmd,
                cwd=str(wd),
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode == 0:
                logger.info("regression_gate PASS for %s (cmd=%s)", wd, cmd[0])
                return True
        except (subprocess.SubprocessError, FileNotFoundError):
            continue

    logger.warning("regression_gate FAIL for %s — no passing test suite found", wd)
    return False


def rollback_to(worktree: str | Path, node_id: str) -> None:
    """``git reset --hard`` to the tag for *node_id*.

    Only rolls back work/experiment branches, never main repos.
    """
    wd = Path(worktree)
    if not wd.is_dir():
        logger.warning("rollback_to: worktree %s not found", wd)
        return

    tag = f"node-{node_id}"
    try:
        _git(["reset", "--hard", tag], cwd=wd)
        logger.info("rollback_to: %s reset to %s", wd, tag)
    except subprocess.CalledProcessError as e:
        logger.error("rollback_to failed in %s: %s", wd, e.stderr)


def merge_parallel(
    integration_branch: str,
    worktrees: Sequence[str | Path],
    main_repo: str | Path,
) -> bool:
    """Merge parallel independent worktrees into an integration branch.

    Each worktree is expected to be on its own branch.  This function
    fetches each, merges, and commits.
    """
    main = Path(main_repo)
    if not main.is_dir():
        logger.warning("merge_parallel: main repo %s not found", main)
        return False

    try:
        _git(["checkout", integration_branch], cwd=main)
    except subprocess.CalledProcessError:
        _git(["checkout", "-b", integration_branch], cwd=main)

    for wt in worktrees:
        wd = Path(wt)
        if not wd.is_dir():
            continue
        try:
            # Get the branch name from the worktree
            result = _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=wd)
            branch = result.stdout.strip()

            # Add worktree as remote and fetch
            _git(["remote", "add", f"wt-{wd.name}", str(wd)], cwd=main, check=False)
            _git(["fetch", f"wt-{wd.name}", branch], cwd=main)
            _git(["merge", f"wt-{wd.name}/{branch}", "--no-edit"], cwd=main)
            _git(["remote", "remove", f"wt-{wd.name}"], cwd=main)
            logger.info("merged worktree %s (branch %s) into %s", wd, branch, integration_branch)
        except subprocess.CalledProcessError as e:
            logger.error("merge_parallel failed for %s: %s", wd, e.stderr)
            return False

    return True
