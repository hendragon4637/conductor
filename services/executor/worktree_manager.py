"""Extracted WorktreeManager — git worktree lifecycle for node execution.

Duplicate of ``backend/worktree/manager.py`` owned by executor-svc for
separation of concerns.  Manages per-node git worktrees under a workspace
root directory.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# Guide 09.4 — the delivered artifacts of a dependency (artifacts mode)
DEP_INCLUDE_ARTIFACTS = [
    "RUN.md",
    ".conductor/workspace.json",
    "exports/**",
    "data/output/**",
]


class WorktreeManager:
    """Manage git worktrees for execution sessions.

    Each session gets a dedicated worktree isolated from the main project
    repo. Worktrees live under ``{workspace_root}/{project_id}/{branch-slug}/``.
    """

    def __init__(self, workspace_root: str | Path):
        self._root = Path(workspace_root).resolve()

    # ------------------------------------------------------------------
    # Project
    # ------------------------------------------------------------------
    def ensure_project(self, project_id: str, repo_path: str | None = None) -> Path:
        """Ensure a project directory exists under the workspace root.

        If ``repo_path`` is given it is registered as the project's working
        copy.  Otherwise a fresh bare repo is initialised.
        """
        project_dir = self._root / project_id
        if repo_path:
            src = Path(repo_path).resolve()
            if not project_dir.exists():
                project_dir.mkdir(parents=True)
                subprocess.run(
                    ["git", "clone", str(src), str(project_dir)],
                    check=True, capture_output=True, timeout=60,
                )
            return project_dir

        if not project_dir.exists():
            project_dir.mkdir(parents=True)
            subprocess.run(
                ["git", "-C", str(project_dir), "init"],
                check=True, capture_output=True, timeout=30,
            )
            subprocess.run(
                ["git", "-C", str(project_dir), "config", "user.email",
                 "conductor@aipc.local"],
                check=True, capture_output=True, timeout=30,
            )
            subprocess.run(
                ["git", "-C", str(project_dir), "config", "user.name", "Conductor"],
                check=True, capture_output=True, timeout=30,
            )
            readme = project_dir / "README.md"
            readme.write_text(f"# {project_id}\n")
            subprocess.run(
                ["git", "-C", str(project_dir), "add", "."],
                check=True, capture_output=True, timeout=30,
            )
            subprocess.run(
                ["git", "-C", str(project_dir), "commit", "-m", "init"],
                check=True, capture_output=True, timeout=30,
            )
        return project_dir

    # ------------------------------------------------------------------
    # Worktree lifecycle
    # ------------------------------------------------------------------
    @staticmethod
    def _branch_slug(branch: str) -> str:
        return re.sub(r"[^a-zA-Z0-9_.-]", "-", branch)

    def create(self, project_id: str, branch: str, kind: str = "work") -> str:
        """Create a git worktree for the given project + branch.

        If the branch already exists (from a prior abandoned session) it
        is force-deleted first so creation succeeds.

        Returns the absolute path to the worktree.
        """
        project_dir = self._root / project_id
        if not project_dir.exists():
            raise FileNotFoundError(
                f"Project {project_id} not found. Call ensure_project() first."
            )

        subprocess.run(
            ["git", "-C", str(project_dir), "branch", "-D", branch],
            capture_output=True, timeout=30,
        )

        slug = self._branch_slug(branch)
        worktree_path = self._root / f"{project_id}.{slug}"

        subprocess.run(
            ["git", "-C", str(project_dir), "worktree", "add",
             "-b", branch, str(worktree_path)],
            check=True, capture_output=True, timeout=60,
        )
        return str(worktree_path)

    def remove(self, project_id: str, worktree_path: str | Path) -> None:
        """Remove a worktree via ``git worktree remove --force`` (safe)."""
        project_dir = self._root / project_id
        wt = Path(worktree_path).resolve()
        subprocess.run(
            ["git", "-C", str(project_dir), "worktree", "remove", "--force", str(wt)],
            check=True, capture_output=True, timeout=60,
        )

    def materialize_deps(
        self,
        project_id: str,
        system_id: str,
        mode: str = "source",
        worktree_path: str | Path | None = None,
    ) -> list[dict[str, str]]:
        """Symlink or copy dependency projects into ``deps/`` for a project.

        Two modes:
          - ``source``: symlink each dependency's project dir into ``deps/<dep_name>/``
          - ``artifacts``: copy only delivered artifacts (DEP_INCLUDE_ARTIFACTS, no source)

        ``deps/`` is created inside ``worktree_path`` when given (the directory
        the agent actually operates in); otherwise it falls back to the project
        checkout dir.

        Returns:
            List of ``{dep_name, dep_project_id, path}`` for each materialized dep.
        """
        import psycopg
        from psycopg.rows import dict_row
        db_url = os.environ.get("DATABASE_URL", "")
        deps_root = Path(worktree_path) if worktree_path else self._root / project_id
        deps_dir = deps_root / "deps"
        deps_dir.mkdir(parents=True, exist_ok=True)

        materialized: list[dict[str, str]] = []

        try:
            with psycopg.connect(db_url, row_factory=dict_row) as c:
                with c.cursor() as cur:
                    cur.execute(
                        """SELECT pd.dep_name, pd.depends_on_project_id, p.name, p.repo_path
                           FROM project_dependencies pd
                           JOIN projects p ON pd.depends_on_project_id = p.project_id
                           WHERE pd.project_id = %s""",
                        (project_id,),
                    )
                    deps = cur.fetchall()
        except Exception:
            logger.exception("Failed to fetch deps for %s", project_id)
            return materialized

        for dep in deps:
            dep_name = dep["dep_name"]
            dep_id = dep["depends_on_project_id"]
            dep_path = self._root / (dep.get("repo_path") or dep_id)
            target = deps_dir / dep_name

            if target.exists():
                materialized.append({"dep_name": dep_name, "dep_project_id": dep_id, "path": str(target)})
                continue

            if mode == "source":
                if dep_path.exists():
                    target.symlink_to(dep_path.resolve(), target_is_directory=True)
                else:
                    # Create placeholder
                    target.mkdir(parents=True)
                    (target / ".placeholder").write_text(f"dependency {dep_name} ({dep_id}) not yet checked out")
            else:
                # artifacts mode: copy only delivered artifacts (guide 09.4)
                target.mkdir(parents=True, exist_ok=True)
                for pattern in DEP_INCLUDE_ARTIFACTS:
                    if pattern.endswith("/**"):
                        rel_dir = pattern[:-3]
                        src_dir = dep_path / rel_dir
                        if src_dir.is_dir():
                            shutil.copytree(src_dir, target / rel_dir, dirs_exist_ok=True)
                    else:
                        src = dep_path / pattern
                        if src.is_file():
                            dst = target / pattern
                            dst.parent.mkdir(parents=True, exist_ok=True)
                            dst.write_text(src.read_text())

            materialized.append({"dep_name": dep_name, "dep_project_id": dep_id, "path": str(target)})
            logger.info("Materialized dep %s → %s (mode=%s)", dep_name, target, mode)

        return materialized

    def list(self, project_id: str | None = None) -> list[dict]:
        """List worktrees for a project repo."""
        if project_id is None:
            return []

        project_dir = self._root / project_id
        if not project_dir.exists():
            return []

        result = subprocess.run(
            ["git", "-C", str(project_dir), "worktree", "list", "--porcelain"],
            capture_output=True, text=True, timeout=30,
        )
        entries: list[dict] = []
        current: dict = {}
        for line in result.stdout.splitlines():
            if line.startswith("worktree "):
                if current:
                    entries.append(current)
                current = {"path": line[9:]}
            elif line.startswith("HEAD "):
                current["head"] = line[5:]
            elif line.startswith("branch "):
                current["branch"] = line[7:]
            elif line == "":
                if current:
                    entries.append(current)
                current = {}
        if current:
            entries.append(current)

        if project_id:
            p = self._root / project_id
            entries = [e for e in entries if str(p) in e.get("path", "")]
        return entries
