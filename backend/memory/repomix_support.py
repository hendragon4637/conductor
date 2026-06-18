import subprocess
from pathlib import Path

from .graphiti_client import add_memory
from .scopes import group_id


def refresh_project_snapshot(project, repo_path):
    snap_dir = Path(repo_path) / ".memory" / "snapshots"
    snap_dir.mkdir(parents=True, exist_ok=True)
    out = snap_dir / "product_repomix.md"
    subprocess.run(
        [
            "npx", "repomix",
            "--style", "markdown",
            "--ignore", "**/node_modules/**,**/.git/**,**/__pycache__/**,*.db",
            "--output", str(out),
            str(repo_path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    group = group_id("product", project)
    add_memory(
        text=out.read_text(),
        group=group,
        source="repomix-snapshot",
    )
    return str(out)
