"""File 08 — Worktree lifecycle: success merge + failure quarantine.

On success: merge the run's verified branch to main, short TTL, clean.
On failure: tag the failed state, quarantine (never merge), longer TTL.
Cleanup removes expired worktrees but preserves git tags for audit.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from backend.config import L4_GATES
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


def _get_plan(plan_id: str) -> dict[str, Any] | None:
    import psycopg
    from psycopg.rows import dict_row
    db_url = _get_db()
    with psycopg.connect(db_url, row_factory=dict_row) as c:
        with c.cursor() as cur:
            cur.execute("SELECT * FROM plans WHERE plan_id = %s", (plan_id,))
            return cur.fetchone()


def _get_active_worktree_root(run_id: str) -> str | None:
    """Find a worktree path from node_sessions for this run.

    Returns the most recent node session's worktree (highest attempt).
    """
    import psycopg
    from psycopg.rows import dict_row
    db_url = _get_db()
    with psycopg.connect(db_url, row_factory=dict_row) as c:
        with c.cursor() as cur:
            cur.execute(
                """SELECT worktree FROM node_sessions
                   WHERE run_id = %s AND worktree IS NOT NULL
                   ORDER BY attempt DESC LIMIT 1""",
                (run_id,),
            )
            row = cur.fetchone()
            return row["worktree"] if row else None


def _get_worktree_branch(worktree_root: str) -> str:
    """Read the actual git branch name from an existing worktree."""
    result = subprocess.run(
        ["git", "-C", worktree_root, "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True, timeout=30,
    )
    result.check_returncode()
    return result.stdout.strip()


def _project_main(run: dict[str, Any], workspace_root: str) -> str:
    project_id = run.get("plan_id", "default")
    project_dir = Path(workspace_root) / project_id
    return str(project_dir / ".git")


def _git_merge(project_dir: str, branch: str, message: str) -> str:
    """Merge ``branch`` into the current branch of ``project_dir``."""
    # Checkout master (or the default branch)
    subprocess.run(
        ["git", "-C", project_dir, "checkout", "master"],
        check=True, capture_output=True, timeout=30,
    )
    # Stash any dirty state so the merge doesn't fail on uncommitted changes
    dirty = subprocess.run(
        ["git", "-C", project_dir, "status", "--porcelain"],
        capture_output=True, text=True, timeout=15,
    )
    if dirty.stdout.strip():
        logger.info("Main repo dirty — stashing %d change(s) before merge", len(dirty.stdout.strip().split("\n")))
        subprocess.run(
            ["git", "-C", project_dir, "stash", "--include-untracked"],
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


def _git_merge_abort(project_dir: str) -> None:
    """Always abort a conflicted merge — a half-merged master is worse."""
    try:
        subprocess.run(
            ["git", "-C", project_dir, "merge", "--abort"],
            capture_output=True, timeout=30,
        )
    except Exception as exc:
        logger.warning("git merge --abort failed in %s: %s", project_dir, exc)


def _summarize_conflict(err: Exception) -> str:
    text = str(err)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return (lines[0] if lines else "merge conflict")[:500]


# ── Merge-blocked escalation (guide 06.5) ───────────────────────────


def pause_project(project_id: str, reason: str) -> None:
    """Pause intake for a project — master didn't advance, so the next goal
    would branch from stale state and re-conflict."""
    import psycopg
    db_url = _get_db()
    with psycopg.connect(db_url) as c:
        with c.cursor() as cur:
            cur.execute(
                """INSERT INTO project_flags (project_id, intake_paused, paused_reason, updated_at)
                   VALUES (%s, true, %s, now())
                   ON CONFLICT (project_id) DO UPDATE SET
                     intake_paused = true, paused_reason = %s, updated_at = now()""",
                (project_id, reason, reason),
            )
        c.commit()


def resume_project(project_id: str) -> None:
    """Clear the intake pause flag for a project."""
    import psycopg
    db_url = _get_db()
    with psycopg.connect(db_url) as c:
        with c.cursor() as cur:
            cur.execute(
                """INSERT INTO project_flags (project_id, intake_paused, updated_at)
                   VALUES (%s, false, now())
                   ON CONFLICT (project_id) DO UPDATE SET
                     intake_paused = false, paused_reason = NULL, updated_at = now()""",
                (project_id,),
            )
        c.commit()


def block_merge(run_id: str, project_id: str, branch: str, error: str) -> dict[str, Any]:
    """Record a blocked merge and pause the project.

    ``outcome`` stays success — quality passed, only integration failed.
    No intake event is emitted on this path (master did not advance).
    """
    _update_run(
        run_id,
        worktree_status="active",
        merge_status="blocked",
        merge_ref=branch,
        merge_error=error,
    )
    pause_project(project_id, reason=f"merge blocked on run {run_id}")
    logger.warning("Run %s merge blocked — project %s paused", run_id, project_id)
    return _get_run(run_id) or {}


def blocked_merge_queue() -> list[dict[str, Any]]:
    """The blocked-merge queue — the partial index IS the queue."""
    import psycopg
    from psycopg.rows import dict_row
    db_url = _get_db()
    with psycopg.connect(db_url, row_factory=dict_row) as c:
        with c.cursor() as cur:
            cur.execute(
                """SELECT id, plan_id, project_id, merge_status, merge_ref, merge_error,
                          worktree_status, created_at
                   FROM runs WHERE merge_status = 'blocked'
                   ORDER BY created_at DESC"""
            )
            return cur.fetchall()


def weekly_blocked_merge_count() -> int:
    """Blocked merges in the last 7 days — should stay near zero; a rising
    count means concurrent goals touch the same paths (a scheduling problem)."""
    import psycopg
    db_url = _get_db()
    with psycopg.connect(db_url) as c:
        with c.cursor() as cur:
            cur.execute(
                """SELECT COUNT(*) FROM runs
                   WHERE merge_status = 'blocked'
                     AND created_at >= NOW() - INTERVAL '7 days'"""
            )
            return cur.fetchone()[0]


def retry_merge(run_id: str, workspace_root: str | None = None) -> dict[str, Any]:
    """Re-attempt a blocked merge; unpauses the project only on success."""
    run = finalize_success(run_id, workspace_root=workspace_root)
    if run.get("merge_status") == "merged":
        resume_project(run.get("project_id") or "")
        logger.info("Retry: run %s merged to main — project resumed", run_id)
    return run


def skip_merge(run_id: str, reason: str) -> dict[str, Any]:
    """Record an abandoned merge and unpause the project."""
    run = _get_run(run_id)
    if not run:
        return {}
    _update_run(run_id, merge_status="skipped", merge_error=(reason or "abandoned")[:500])
    resume_project(run.get("project_id") or "")
    logger.info("Run %s merge skipped: %s", run_id, reason)
    return _get_run(run_id) or {}


# ── Image pipeline (guide 06.6, opt-in container variant) ───────────


def _load_manifest(project_dir: str) -> dict[str, Any] | None:
    p = Path(project_dir) / ".conductor" / "workspace.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("Failed to parse manifest %s", p)
        return None


def _image_name(project_id: str) -> str:
    name = re.sub(r"[^a-z0-9._-]", "-", project_id.lower()).strip(".-") or "conductor"
    return f"conductor/{name}"


def _master_sha(project_dir: str) -> str:
    result = subprocess.run(
        ["git", "-C", project_dir, "rev-parse", "HEAD"],
        capture_output=True, text=True, timeout=30,
    )
    return result.stdout.strip()


def _image_exists(image_ref: str) -> bool:
    result = subprocess.run(
        ["docker", "image", "inspect", image_ref],
        capture_output=True, text=True, timeout=60,
    )
    return result.returncode == 0


def _docker_tag(src: str, dst: str) -> None:
    result = subprocess.run(
        ["docker", "tag", src, dst],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"docker tag {src} -> {dst} failed: {result.stderr.strip()[:300]}")


def _prune_image_tags(image_name: str, keep: str, retention: int = 3) -> None:
    """Remove old sha tags, keeping the newest ``retention`` per component."""
    prefix = image_name + ":"
    result = subprocess.run(
        ["docker", "images", "--format", "{{.Repository}}:{{.Tag}}|{{.CreatedAt}}"],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        return
    tagged: list[tuple[str, str]] = []
    for line in result.stdout.splitlines():
        ref, _, created = line.partition("|")
        if ref.startswith(prefix):
            tagged.append((ref, created))
    created_by = dict(tagged)
    sha_tags = sorted(
        (ref for ref, _ in tagged
         if ref not in (f"{image_name}:latest", f"{image_name}:candidate", keep)),
        key=lambda r: created_by.get(r, ""),
        reverse=True,
    )
    for old in sha_tags[retention - 1:]:
        subprocess.run(["docker", "rmi", old], capture_output=True, text=True, timeout=120)


def _run_pre_merge_checks(worktree_root: str, project_dir: str, run: dict[str, Any]) -> str | None:
    """Run pre-merge gates on the candidate branch; return error text or None.

    ``bash gates.sh`` runs only where the generated root gate exists
    (subdirs/mixed layouts).  The container image build is opt-in via
    ``manifest.variants.container``.  A failure blocks the merge — master
    never receives an unbuildable component.
    """
    manifest = _load_manifest(project_dir)
    checks: list[list[str]] = []
    if (Path(worktree_root) / "gates.sh").exists():
        checks.append(["bash", "gates.sh"])
    if (manifest or {}).get("variants", {}).get("container"):
        image = _image_name(run.get("project_id") or run.get("plan_id", "default"))
        checks.append(
            ["docker", "build", "-t", f"{image}:candidate",
             "-f", "variants/container/Dockerfile", "."]
        )
    if not checks:
        return None
    for cmd in checks:
        result = subprocess.run(
            cmd, cwd=worktree_root, capture_output=True, text=True, timeout=1800,
        )
        if result.returncode != 0:
            tail = (result.stderr or result.stdout).strip().splitlines()
            detail = " | ".join(tail[-3:])[:500] if tail else f"{cmd[0]} failed"
            return f"pre-merge check failed: {' '.join(cmd)}: {detail}"
    return None


def finalize_image(run_id: str, workspace_root: str | None = None) -> dict[str, Any]:
    """Post-merge image finalize: re-tag ``candidate`` with the real sha.

    Verifies the new tag exists BEFORE pruning.  Failures are recorded on the
    run (``image_status='failed'``) and escalated — they never change the run
    outcome, which is already success.
    """
    _wsr = workspace_root or os.environ.get(
        "WORKSPACE_ROOT", "/opt/aipc/conductor/workspace"
    )
    run = _get_run(run_id)
    if not run:
        return {}
    plan = _get_plan(run["plan_id"])
    project_id = plan["project_id"] if plan else run.get("project_id", run.get("plan_id", "default"))
    project_dir = str(Path(_wsr) / project_id)

    manifest = _load_manifest(project_dir)
    if not (manifest or {}).get("variants", {}).get("container"):
        _update_run(run_id, image_status="skipped")
        return _get_run(run_id) or {}

    name = _image_name(project_id)
    try:
        sha6 = _master_sha(project_dir)[:6]
        new = f"{name}:{sha6}"
        _docker_tag(f"{name}:candidate", new)
        if not _image_exists(new):
            raise RuntimeError(f"image {new} missing after tag")
        _docker_tag(new, f"{name}:latest")
        _prune_image_tags(name, keep=new)
        _update_run(run_id, image_status="built", image_tag=new)
        logger.info("Image finalized for run %s: %s", run_id, new)
    except Exception as exc:
        _update_run(run_id, image_status="failed", image_error=str(exc)[:500])
        logger.error("Image finalize failed for run %s: %s", run_id, exc)
    return _get_run(run_id) or {}


def finalize_success(run_id: str, workspace_root: str | None = None) -> dict[str, Any]:
    """Merge the run's verified worktree branch to main.

    Args:
        run_id: The run to finalize.
        workspace_root: Workspace root path (defaults to env).

    Returns:
        The updated run dict.  ``merge_status`` is ``merged`` on success or
        ``blocked`` when pre-merge checks or the merge itself failed — in the
        blocked case the merge is aborted, the project is paused, and no
        intake event is emitted (guide 06.5).
    """
    _wsr = workspace_root or os.environ.get(
        "WORKSPACE_ROOT", "/opt/aipc/conductor/workspace"
    )
    run = _get_run(run_id)
    if not run:
        raise ValueError(f"Run {run_id} not found")

    plan = _get_plan(run["plan_id"])
    project_id = plan["project_id"] if plan else run.get("project_id", run.get("plan_id", "default"))
    project_dir = str(Path(_wsr) / project_id)

    worktree_root = _get_active_worktree_root(run_id)
    if not worktree_root:
        raise ValueError(
            f"No worktree found for run {run_id} in node_sessions"
        )
    branch = _get_worktree_branch(worktree_root)

    goal = run.get("note") or f"run {run_id}"
    merge_msg = f"run {run_id}: {goal}"

    check_error = _run_pre_merge_checks(worktree_root, project_dir, run)
    if check_error:
        logger.warning("Pre-merge checks failed for run %s: %s", run_id, check_error)
        block_merge(run_id, project_id, branch, check_error)
        return _get_run(run_id) or {}

    try:
        merge_commit = _git_merge(project_dir, branch, merge_msg)
        _update_run(
            run_id,
            worktree_status="merged",
            merge_commit=merge_commit,
            merge_status="merged",
        )
        _sched_cleanup(run_id, SUCCESS_TTL_DAYS)
        logger.info("Run %s merged to main at %s", run_id, merge_commit)
    except RuntimeError as exc:
        logger.warning("Merge conflict for run %s: %s", run_id, exc)
        _git_merge_abort(project_dir)
        block_merge(run_id, project_id, branch, _summarize_conflict(exc))

    return _get_run(run_id) or {}


def _run_l4(run_id: str, plan: dict[str, Any], run: dict[str, Any]) -> None:
    if not L4_GATES:
        logger.info(
            "L4_GATES disabled — skipping L4 evaluation for run %s "
            + "(set L4_GATES=true to enable)",
            run_id,
        )
        _update_run(run_id, l4_status="skipped", l4_reason="L4_GATES disabled")
        return

    try:
        from backend.evaluator.l4_persona.simulate import run_l4_plan

        base_url = os.environ.get("L4_BASE_URL", "http://127.0.0.1:8000")
        product_type = os.environ.get("L4_PRODUCT_TYPE", "api")
        l4_result = run_l4_plan(
            plan=plan, run=run,
            product_type=product_type, base_url=base_url,
        )

        _update_run(
            run_id,
            l4_standalone=l4_result.get("l4_standalone"),
            l4_acceptance=l4_result.get("l4_acceptance"),
            l4_status="done",
            l4_reason="",
        )
        logger.info(
            "L4 for run %s: standalone=%s acceptance=%s driver=%s",
            run_id, l4_result.get("l4_standalone"),
            l4_result.get("l4_acceptance"), l4_result.get("driver"),
        )
    except Exception as exc:
        logger.warning("L4 simulation failed for run %s: %s", run_id, exc)
        _update_run(run_id, l4_status="failed", l4_reason=str(exc)[:500])


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

    plan = _get_plan(run["plan_id"])
    project_id = plan["project_id"] if plan else run.get("plan_id", "default")
    project_dir = str(Path(_wsr) / project_id)

    worktree_root = _get_active_worktree_root(run_id)
    if worktree_root:
        branch = _get_worktree_branch(worktree_root)
    else:
        logger.warning("No worktree found for run %s in node_sessions", run_id)
        branch = f"run/{run['plan_id']}/{run_id}"

    tag = f"failed/{run['plan_id']}/{run_id}"

    # Tag the failed state (preserves exact state for debugging)
    try:
        _git_tag(project_dir, branch, tag, f"FAILED: {reason}")
        logger.info("Tagged failed run %s as %s", run_id, tag)
    except Exception as exc:
        logger.warning("Failed to tag run %s: %s", run_id, exc)

    # Quarantine worktree
    worktree_root = worktree_root or run.get("worktree_root")
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
