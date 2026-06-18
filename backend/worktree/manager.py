import os
import re
import subprocess
from pathlib import Path


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
            # initial commit so worktree can be created
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

        # Clean up stale branch first
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
        """Remove a worktree via ``git worktree remove --force`` (safe).

        ``--force`` is required because the worktree contains untracked/
        modified files (agent config was written into it).
        """
        project_dir = self._root / project_id
        wt = Path(worktree_path).resolve()
        subprocess.run(
            ["git", "-C", str(project_dir), "worktree", "remove", "--force", str(wt)],
            check=True, capture_output=True, timeout=60,
        )

    def list(self, project_id: str | None = None) -> list[dict]:
        """List worktrees for a project repo.

        Requires ``project_id`` — runs ``git worktree list`` inside the
        project's local repo so it returns correct results.
        """
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
