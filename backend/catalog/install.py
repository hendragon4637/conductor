"""Catalog-driven install flow — installs tools by kind.

Guard: only install if status='vetted' AND status_by='human'.
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Any

import psycopg
import yaml

logger = logging.getLogger(__name__)


def get_db_url() -> str:
    return os.environ["DATABASE_URL"]


class InstallError(Exception):
    """Raised when a tool install fails."""
    pass


def resolve_catalog_entry(db_url: str, name_or_id: str) -> dict[str, Any] | None:
    """Look up a catalog entry by name or ID. Only returns vetted+human entries."""
    with psycopg.connect(db_url) as conn, conn.cursor() as cur:
        # Try by name first, then by ID
        cur.execute(
            """SELECT id, name, description, kind, source_url, license, stars, metadata
                 FROM tool_catalog
                WHERE (name = %s OR id::text = %s)
                  AND status = 'vetted'
                  AND status_by = 'human'
                LIMIT 1""",
            (name_or_id, name_or_id),
        )
        row = cur.fetchone()
    if not row:
        return None
    return {
        "id": str(row[0]),
        "name": row[1],
        "description": row[2],
        "kind": row[3],
        "source_url": row[4],
        "license": row[5],
        "stars": row[6],
        "metadata": row[7] if isinstance(row[7], dict) else {},
    }


def install_skill(entry: dict[str, Any], target_dir: str | Path) -> dict[str, Any]:
    """Install a skill into the skills_store.

    Creates a skill directory in skills_store/ with SKILL.md derived from
    the catalog entry metadata.
    """
    skills_store = Path(os.environ.get("SKILLS_STORE", "/opt/aipc/conductor/skills_store"))
    skill_dir = skills_store / entry["name"]
    skill_dir.mkdir(parents=True, exist_ok=True)

    # Generate SKILL.md from catalog entry
    description = entry.get("description", "")
    source_url = entry.get("source_url", "")

    skill_md = f"""# {entry['name']}

{description}

## Source
{source_url}

## Install
This skill was installed from the tool catalog (vetted by human).
"""
    (skill_dir / "SKILL.md").write_text(skill_md)

    # Also install to target worktree's .opencode/skills/ if target_dir provided
    target = Path(target_dir) / ".opencode" / "skills" / entry["name"]
    target.mkdir(parents=True, exist_ok=True)
    (target / "SKILL.md").write_text(skill_md)

    return {"status": "installed", "kind": "skill", "name": entry["name"], "path": str(skill_dir)}


def install_mcp(entry: dict[str, Any], target_dir: str | Path) -> dict[str, Any]:
    """Install an MCP server configuration.

    Appends to or creates mcp_config.yaml in the worktree.
    """
    target = Path(target_dir)
    mcp_config_path = target / "mcp_config.yaml"

    mcp_entry = {
        "name": entry["name"],
        "description": entry.get("description", ""),
        "url": entry.get("source_url", ""),
        "transport": "sse",  # default
        "enabled": True,
    }

    existing = {}
    if mcp_config_path.exists():
        with open(mcp_config_path) as f:
            existing = yaml.safe_load(f) or {}

    mcp_servers = existing.get("mcp_servers", [])
    # Don't add duplicates
    if not any(s.get("name") == entry["name"] for s in mcp_servers):
        mcp_servers.append(mcp_entry)

    existing["mcp_servers"] = mcp_servers
    with open(mcp_config_path, "w") as f:
        yaml.dump(existing, f, default_flow_style=False)

    return {"status": "installed", "kind": "mcp", "name": entry["name"], "config_key": entry["name"]}


def install_cli(entry: dict[str, Any], target_dir: str | Path) -> dict[str, Any]:
    """Install a CLI tool.

    Attempts npm/pip/cargo install based on heuristics from source_url or metadata.
    """
    source_url = entry.get("source_url", "") or ""
    target = Path(target_dir)

    # Heuristic: determine package manager from source URL or name patterns
    name = entry["name"]

    # Try npm first (common for JS tools)
    try:
        subprocess.run(
            ["npm", "install", "-g", name],
            capture_output=True, text=True, timeout=120,
        )
        return {"status": "installed", "kind": "cli", "name": name, "method": "npm"}
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    # Try pip
    try:
        subprocess.run(
            ["pip", "install", name],
            capture_output=True, text=True, timeout=120,
        )
        return {"status": "installed", "kind": "cli", "name": name, "method": "pip"}
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    # Try pip3
    try:
        subprocess.run(
            ["pip3", "install", name],
            capture_output=True, text=True, timeout=120,
        )
        return {"status": "installed", "kind": "cli", "name": name, "method": "pip3"}
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    raise InstallError(f"Could not install CLI tool '{name}' — no matching package manager")


def install_tool(db_url: str, name_or_id: str, target_dir: str | Path) -> dict[str, Any]:
    """Main install function — resolves catalog entry and dispatches by kind.

    Args:
        db_url: Database URL.
        name_or_id: Tool name or UUID from tool_catalog.
        target_dir: Target worktree path for installation.

    Returns:
        Dict with status, kind, name, and install details.

    Raises:
        InstallError: If tool is not found, not vetted, or install fails.
    """
    entry = resolve_catalog_entry(db_url, name_or_id)
    if not entry:
        raise InstallError(
            f"Tool '{name_or_id}' not found or not vetted by human in catalog"
        )

    kind = entry["kind"]
    if kind == "skill":
        return install_skill(entry, target_dir)
    elif kind == "mcp":
        return install_mcp(entry, target_dir)
    elif kind == "cli":
        return install_cli(entry, target_dir)
    else:
        raise InstallError(f"Unknown tool kind: {kind}")
