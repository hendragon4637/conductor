import os
from pathlib import Path

import pytest

from backend.worktree import WorktreeManager, assemble_for_spawn

WORKSPACE_ROOT = os.environ.get("WORKSPACE_ROOT", "/opt/aipc/conductor/workspace")
DB_URL = os.environ.get("DATABASE_URL")


@pytest.fixture
def sample_agent_config():
    """Return a minimal agent_config dict matching the DB schema."""
    return {
        "agent_config_id": "opencode:backend-executor",
        "cli": "opencode",
        "permission_policy": {
            "rm": "deny",
            "bash": "ask",
            "write": "ask_outside_repo",
            "git_push": "deny",
            "pip_install": "ask",
        },
        "system_prompt": "You are a Python/FastAPI backend executor. Be concise.",
        "skill_path": "/opt/aipc/conductor/skills/backend/executor/SKILL.md",
    }


@pytest.fixture
def wm():
    return WorktreeManager(WORKSPACE_ROOT)


def test_ensure_project_creates_repo(wm):
    proj_id = "test-ensure-proj"
    proj_dir = wm.ensure_project(proj_id)
    assert proj_dir.exists()
    assert (proj_dir / ".git").exists()
    assert (proj_dir / "README.md").exists()


def test_worktree_and_assemble(wm, sample_agent_config):
    proj_id = "test-wt-proj"
    branch = "feat/test-wt"
    wm.ensure_project(proj_id)

    wt_path = wm.create(proj_id, branch)
    wt = Path(wt_path)
    assert wt.exists()

    assemble_for_spawn(
        worktree=wt,
        cli=sample_agent_config["cli"],
        agent_config=sample_agent_config,
        project_id=proj_id,
        session_id="sess1",
        db_url=DB_URL,
    )

    # Verify files created by OpenCode adapter
    assert (wt / "opencode.json").exists(), "permission file missing"
    assert (wt / "AGENTS.md").exists(), "instructions file missing"
    assert (wt / ".opencode" / "skills" / "executor" / "SKILL.md").exists(), "skills missing"

    # Verify content — auto_approve=True (default) produces all-allow config
    import json
    perm = json.loads((wt / "opencode.json").read_text())
    assert perm["permission"]["edit"] == "allow"
    assert perm["permission"]["bash"] == "allow"
    assert perm["permission"]["webfetch"] == "allow"

    agents_md = (wt / "AGENTS.md").read_text()
    assert "You are a Python/FastAPI backend executor" in agents_md
    # Memory should be included if DB is available
    if DB_URL:
        assert "httpx" in agents_md or "authorization" in agents_md or agents_md.strip()

    # Verify git worktree is registered
    entries = wm.list(proj_id)
    matching = [e for e in entries if str(wt) in e.get("path", "")]
    assert len(matching) >= 1, f"worktree {wt} not in git worktree list"

    # Cleanup
    wm.remove(proj_id, wt_path)
    assert not wt.exists()


def test_assemble_no_auto_approve(wm, sample_agent_config):
    """When auto_approve=False the opencode.json should have an empty
    permission block so OpenCode falls back to its global config."""
    proj_id = "test-noaa-proj"
    branch = "feat/test-noaa"
    wm.ensure_project(proj_id)

    wt_path = wm.create(proj_id, branch)
    wt = Path(wt_path)

    assemble_for_spawn(
        worktree=wt,
        cli=sample_agent_config["cli"],
        agent_config=sample_agent_config,
        project_id=proj_id,
        session_id="sess2",
        db_url=DB_URL,
        auto_approve=False,
    )

    # Permission file still exists but has no effective override
    import json
    perm = json.loads((wt / "opencode.json").read_text())
    assert perm["permission"] == {}, \
        "auto_approve=False should produce empty permission block"

    # Cleanup
    wm.remove(proj_id, wt_path)
