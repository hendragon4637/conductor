"""File 08 — Worktree lifecycle: success merge + failure quarantine.

On success: merge the run's verified branch to main, short TTL, clean.
On failure: tag the failed state, quarantine (never merge), longer TTL.
Cleanup removes expired worktrees but preserves git tags for audit.
"""

from __future__ import annotations

import logging
import os
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from backend.worktree.manager import WorktreeManager

logger = logging.getLogger(__name__)

SUCCESS_TTL_DAYS = int(os.environ.get("SUCCESS_WORKTREE_TTL_DAYS", "1"))
FAILED_TTL_DAYS = int(os.environ.get("FAILED_WORKTREE_TTL_DAYS", "7"))


def _get_db() -> str:
    return os.environ["DATABASE_URL"]


def _update_run(run_id: str, **kwargs) -> None:
    import psycopg
    if not kwargs:
        return
    db_url = _get_db()
    sets = ", ".join(f"{k} = %s" for k in kwargs)
    vals = list(kwargs.values())
    with psycopg.connect(db_url) as c:
        with c.cursor() as cur:
            cur.execute(
                f"UPDATE runs SET {sets} WHERE id = %s",
                (*vals, run_id),
            )
        c.commit()


def _get_run(run_id: str) -> dict[str, Any] | None:
    import psycopg
    from psycopg.rows import dict_row
    db_url = _get_db()
    with psycopg.connect(db_url, row_factory=dict_row) as c:
        with c.cursor() as cur:
            cur.execute("SELECT * FROM runs WHERE id = %s", (run_id,))
            return cur.fetchone()


def _run_branch(run: dict[str, Any]) -> str:
    return f"run/{run['plan_id']}/{run['id']}"


def _project_main(run: dict[str, Any], workspace_root: str) -> str:
    project_id = run.get("plan_id", "default")
    project_dir = Path(workspace_root) / project_id
    return str(project_dir / ".git")


def _git_merge(project_dir: str, branch: str, message: str) -> str:
    """Merge ``branch`` into the current branch of ``project_dir``."""
    # Fetch the branch reference
    subprocess.run(
        ["git", "-C", project_dir, "fetch", ".", f"{branch}:{branch}"],
        check=True, capture_output=True, timeout=30,
    )
    # Checkout main (or the default branch)
    subprocess.run(
        ["git", "-C", project_dir, "checkout", "main"],
        check=True, capture_output=True, timeout=30,
    )
    # Merge
    result = subprocess.run(
        ["git", "-C", project_dir, "merge", "--no-ff", branch, "-m", message],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Merge conflict for branch {branch}: {result.stderr}"
        )
    # Get merge commit SHA
    sha_result = subprocess.run(
        ["git", "-C", project_dir, "rev-parse", "HEAD"],
        capture_output=True, text=True, timeout=30,
    )
    return sha_result.stdout.strip()


def _git_tag(project_dir: str, branch: str, tag: str, message: str) -> None:
    """Tag the HEAD of a branch without switching branches."""
    subprocess.run(
        ["git", "-C", project_dir, "tag", "-f", tag, branch, "-m", message],
        check=True, capture_output=True, timeout=30,
    )


def _quarantine_worktree(worktree_root: str, plan_id: str, run_id: str) -> None:
    """Move failed worktree to a quarantine area."""
    quarantine_dir = Path(worktree_root).parent / "quarantine" / f"{plan_id}_{run_id}"
    quarantine_dir.parent.mkdir(parents=True, exist_ok=True)
    if Path(worktree_root).exists():
        Path(worktree_root).rename(quarantine_dir)
        logger.info("Quarantined worktree %s → %s", worktree_root, quarantine_dir)


def _sched_cleanup(run_id: str, ttl_days: int) -> None:
    """Set the worktree_expires_at for TTL cleanup."""
    expires = datetime.now(timezone.utc) + timedelta(days=ttl_days)
    _update_run(run_id, worktree_expires_at=expires)


def finalize_success(run_id: str, workspace_root: str | None = None) -> dict[str, Any]:
    """Merge the run's verified worktree branch to main.

    Args:
        run_id: The run to finalize.
        workspace_root: Workspace root path (defaults to env).

    Returns:
        The updated run dict.

    Raises:
        RuntimeError: On merge conflict (flagged for human).
    """
    _wsr = workspace_root or os.environ.get(
        "WORKSPACE_ROOT", "/opt/aipc/conductor/workspace"
    )
    run = _get_run(run_id)
    if not run:
        raise ValueError(f"Run {run_id} not found")

    project_id = run.get("plan_id", "default")
    branch = _run_branch(run)
    project_dir = str(Path(_wsr) / project_id)

    goal = run.get("note") or f"run {run_id}"
    merge_msg = f"run {run_id}: {goal}"

    try:
        merge_commit = _git_merge(project_dir, branch, merge_msg)
        _update_run(
            run_id,
            worktree_status="merged",
            merge_commit=merge_commit,
        )
        _sched_cleanup(run_id, SUCCESS_TTL_DAYS)
        logger.info("Run %s merged to main at %s", run_id, merge_commit)
    except RuntimeError as exc:
        logger.warning("Merge conflict for run %s: %s", run_id, exc)
        _update_run(run_id, worktree_status="active")  # leave active for human
        raise

    return _get_run(run_id) or {}


def finalize_failure(run_id: str, reason: str, workspace_root: str | None = None) -> dict[str, Any]:
    """Quarantine a failed run's worktree.

    Tags the failed state, quarantines the worktree (never merges),
    and sets a longer TTL for debugging.

    Args:
        run_id: The failed run.
        reason: Human-readable failure reason.
        workspace_root: Workspace root path.

    Returns:
        The updated run dict.
    """
    _wsr = workspace_root or os.environ.get(
        "WORKSPACE_ROOT", "/opt/aipc/conductor/workspace"
    )
    run = _get_run(run_id)
    if not run:
        raise ValueError(f"Run {run_id} not found")

    project_id = run.get("plan_id", "default")
    branch = _run_branch(run)
    tag = f"failed/{run['plan_id']}/{run_id}"
    project_dir = str(Path(_wsr) / project_id)

    # Tag the failed state (preserves exact state for debugging)
    try:
        _git_tag(project_dir, branch, tag, f"FAILED: {reason}")
        logger.info("Tagged failed run %s as %s", run_id, tag)
    except Exception as exc:
        logger.warning("Failed to tag run %s: %s", run_id, exc)

    # Quarantine worktree
    worktree_root = run.get("worktree_root")
    if worktree_root and Path(worktree_root).exists():
        try:
            _quarantine_worktree(worktree_root, project_id, run_id)
        except Exception as exc:
            logger.warning("Failed to quarantine worktree %s: %s", worktree_root, exc)

    _update_run(
        run_id,
        worktree_status="quarantined",
        quarantine_tag=tag,
        worktree_expires_at=datetime.now(timezone.utc) + timedelta(days=FAILED_TTL_DAYS),
    )

    return _get_run(run_id) or {}


def cleanup_expired(workspace_root: str | None = None) -> list[str]:
    """Remove expired worktrees for merged/quarantined runs.

    Keeps git tags for audit trail. Never touches active or
    merge-conflicted worktrees.

    Args:
        workspace_root: Workspace root path.

    Returns:
        List of cleaned run IDs.
    """
    _wsr = workspace_root or os.environ.get(
        "WORKSPACE_ROOT", "/opt/aipc/conductor/workspace"
    )
    import psycopg
    from psycopg.rows import dict_row

    db_url = _get_db()
    cleaned: list[str] = []

    try:
        with psycopg.connect(db_url, row_factory=dict_row) as c:
            with c.cursor() as cur:
                cur.execute(
                    """SELECT id, worktree_root, worktree_status, project_id
                       FROM runs
                       WHERE worktree_status IN ('merged', 'quarantined')
                         AND worktree_expires_at IS NOT NULL
                         AND worktree_expires_at < NOW()
                         AND worktree_root IS NOT NULL
                    """
                )
                expired = cur.fetchall()
    except Exception as exc:
        logger.warning("cleanup_expired DB query failed: %s", exc)
        return cleaned

    for run in expired:
        wt_root = run.get("worktree_root")
        if not wt_root or not Path(wt_root).exists():
            continue
        try:
            project_id = run.get("plan_id", "default")
            wm = WorktreeManager(_wsr)
            wm.remove(project_id, wt_root)
        except Exception as exc:
            logger.warning("Failed to remove worktree %s: %s", wt_root, exc)
            continue

        rid = run["id"]
        try:
            _update_run(rid, worktree_status="cleaned")
        except Exception as exc:
            logger.warning("Failed to mark run %s as cleaned: %s", rid, exc)

        cleaned.append(rid)
        logger.info("Cleaned up expired worktree for run %s", rid)

    return cleaned
