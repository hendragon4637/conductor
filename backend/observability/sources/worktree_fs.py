"""Source adapter: Filesystem/git worktree → progress signals.

Tracks file modifications, git diffs, and test results in a worktree
to provide "ground-truth side-effect" signals for the watcher.
"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Iterator


def worktree_events(worktree_path: str | Path) -> Iterator[dict]:
    """Yield events about file-system changes in a worktree.

    Each event has:
        ts (float): unix timestamp
        source (str): "worktree_fs"
        type (str): "git_dirty" | "file_modified" | "test_result"
        content (str): description
        metadata (dict): extra info
    """
    wd = Path(worktree_path)
    if not wd.is_dir():
        return

    now = time.time()

    # Git dirty check
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=wd,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            lines = result.stdout.strip().splitlines()
            yield {
                "ts": now,
                "source": "worktree_fs",
                "type": "git_dirty",
                "role": None,
                "content": f"{len(lines)} uncommitted change(s)",
                "tokens": {},
                "metadata": {
                    "worktree": str(wd),
                    "changed_files": len(lines),
                    "porcelain": result.stdout.strip()[:500],
                },
            }
    except (subprocess.SubprocessError, FileNotFoundError):
        pass

    # Look for test result files (pytest exit code markers)
    for marker in [".pytest_ran_ok", ".pytest_ran_failed"]:
        marker_path = wd / marker
        if marker_path.exists():
            mtime = marker_path.stat().st_mtime
            yield {
                "ts": mtime,
                "source": "worktree_fs",
                "type": "test_result",
                "role": None,
                "content": f"Tests {'passed' if 'ok' in marker else 'failed'}",
                "tokens": {},
                "metadata": {
                    "worktree": str(wd),
                    "marker": marker,
                    "passed": "ok" in marker,
                },
            }

    # Recently modified files (last 5 minutes)
    cutoff = now - 300
    try:
        for f in wd.rglob("*"):
            if f.is_file() and f.stat().st_mtime > cutoff:
                # Skip hidden dirs and common ignores
                rel = f.relative_to(wd)
                if any(p.startswith(".") for p in rel.parts):
                    continue
                if f.suffix in (".pyc", ".pyo", ".cache"):
                    continue
                yield {
                    "ts": f.stat().st_mtime,
                    "source": "worktree_fs",
                    "type": "file_modified",
                    "role": None,
                    "content": str(rel),
                    "tokens": {},
                    "metadata": {
                        "worktree": str(wd),
                        "file": str(rel),
                        "mtime": f.stat().st_mtime,
                    },
                }
    except PermissionError:
        pass


def git_diff_stat(worktree_path: str | Path) -> dict:
    """Return ``{insertions, deletions, files_changed}`` from ``git diff --stat``."""
    wd = Path(worktree_path)
    if not wd.is_dir():
        return {"insertions": 0, "deletions": 0, "files_changed": 0}
    try:
        result = subprocess.run(
            ["git", "diff", "--stat"],
            cwd=wd,
            capture_output=True,
            text=True,
            timeout=10,
        )
        # Parse "1 file changed, 2 insertions(+), 1 deletion(-)"
        stats = {"insertions": 0, "deletions": 0, "files_changed": 0}
        for line in result.stdout.strip().splitlines():
            if not line:
                continue
            import re

            m = re.search(r"(\d+)\s+file", line)
            if m:
                stats["files_changed"] = int(m.group(1))
            m = re.search(r"(\d+)\s+insertion", line)
            if m:
                stats["insertions"] = int(m.group(1))
            m = re.search(r"(\d+)\s+deletion", line)
            if m:
                stats["deletions"] = int(m.group(1))
        return stats
    except (subprocess.SubprocessError, FileNotFoundError):
        return {"insertions": 0, "deletions": 0, "files_changed": 0}


def fs_changed_recently(worktree_path: str | Path, window_s: int = 300) -> bool:
    """True if any file in the worktree was modified within *window_s* seconds."""
    wd = Path(worktree_path)
    if not wd.is_dir():
        return False
    cutoff = time.time() - window_s
    try:
        for f in wd.rglob("*"):
            if f.is_file() and f.stat().st_mtime > cutoff:
                return True
    except PermissionError:
        pass
    return False
