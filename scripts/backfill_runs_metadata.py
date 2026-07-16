"""Backfill runs.master_commit and node_sessions.commit_tag from existing git data.

Reads planning branches (master HEAD at worktree creation time) and git tags
in worktrees to populate the new metadata columns for existing data.

Idempotent: skips rows that already have values.  Safe to re-run.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("backfill")

WORKSPACE_ROOT = "/opt/aipc/conductor/workspace"

DB_DSN = os.environ.get("DATABASE_URL", "")


def _git(workdir: str, *args: str) -> str | None:
    try:
        res = subprocess.run(
            ["git"] + list(args),
            cwd=workdir, capture_output=True, text=True, timeout=15, check=True,
        )
        return res.stdout.strip()
    except Exception:
        return None


def backfill_master_commit(cur) -> None:
    """Set runs.master_commit from the planning-{plan_id} branch."""
    cur.execute("SELECT id, plan_id, project_id FROM runs WHERE master_commit IS NULL")
    rows = cur.fetchall()
    logger.info("=== master_commit: %d runs need backfill ===", len(rows))
    for r in rows:
        run_id = r[0]
        plan_id = r[1]
        project_id = r[2]

        planning_wt = os.path.join(WORKSPACE_ROOT, f"{project_id}.{plan_id}-planning")
        master_commit = None
        if os.path.isdir(planning_wt):
            master_commit = _git(planning_wt, "rev-parse", "HEAD")

        if not master_commit:
            project_dir = os.path.join(WORKSPACE_ROOT, project_id)
            branch = f"planning-{plan_id}"
            if os.path.isdir(project_dir):
                master_commit = _git(project_dir, "rev-parse", branch)

        if master_commit:
            cur.execute(
                "UPDATE runs SET master_commit = %s WHERE id = %s",
                (master_commit, run_id),
            )
            logger.info("  %s → master_commit=%s (from %s)", run_id, master_commit[:8], project_id)
        else:
            logger.warning("  %s → SKIP (no planning branch/worktree found for %s)", run_id, plan_id)


def backfill_commit_tag(cur) -> None:
    """Set node_sessions.commit_tag from git tags in worktrees and project repos."""
    cur.execute("""
        SELECT ns.id, ns.run_id, ns.node_id, ns.worktree, r.project_id
        FROM node_sessions ns
        JOIN runs r ON ns.run_id = r.id
        WHERE ns.commit_tag IS NULL
          AND ns.node_id NOT LIKE '\\_planning'
          AND ns.verdict IS NOT NULL
          AND ns.verdict NOT IN ('pending', 'running')
    """)
    rows = cur.fetchall()
    logger.info("=== commit_tag: %d node_sessions need backfill ===", len(rows))
    for r in rows:
        ns_id = r[0]
        node_id = r[2]
        worktree_path = r[3]
        project_id = r[4]
        tag = f"node-{node_id}"

        sha = None
        source = None

        # 1. Try worktree git repo directly
        if worktree_path and os.path.isdir(worktree_path):
            sha = _git(worktree_path, "rev-parse", tag)
            source = "worktree"

        # 2. Try tags in master project repo (merged runs)
        if not sha:
            project_dir = os.path.join(WORKSPACE_ROOT, project_id)
            if os.path.isdir(project_dir):
                sha = _git(project_dir, "rev-parse", tag)
                source = "project"

        # 3. Try quarantine dir
        if not sha:
            quarantine = os.path.join(WORKSPACE_ROOT, "quarantine")
            if os.path.isdir(quarantine):
                for qdir in os.listdir(quarantine):
                    qpath = os.path.join(quarantine, qdir)
                    if os.path.isdir(qpath):
                        sha = _git(qpath, "rev-parse", tag)
                        if sha:
                            source = f"quarantine/{qdir}"
                            break

        if sha:
            cur.execute(
                "UPDATE node_sessions SET commit_tag = %s WHERE id = %s",
                (tag, ns_id),
            )
            logger.info("  %s → commit_tag=%s (from %s)", ns_id, tag, source)
        else:
            logger.info("  %s → SKIP (no tag %s found)", ns_id, tag)


def main() -> None:
    dsn = DB_DSN or "postgresql://aipc@localhost:5432/aipc_conductor"
    import psycopg
    conn = psycopg.connect(dsn)
    with conn:
        with conn.cursor() as cur:
            backfill_master_commit(cur)
            backfill_commit_tag(cur)
    logger.info("Done.")


if __name__ == "__main__":
    main()
