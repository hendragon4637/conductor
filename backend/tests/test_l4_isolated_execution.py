from __future__ import annotations

import json
import os
from pathlib import Path


os.environ.setdefault("DATABASE_URL", "postgresql://test:test@127.0.0.1:5432/test")
os.environ.setdefault("RABBIT_URL", "amqp://guest:guest@127.0.0.1:5672/%2F")
os.environ.setdefault("SERVICE_NAME", "evaluator")

import pytest

from services.evaluator.main import (
    _cleanup_l4_workspace,
    _is_install_command,
    _l4_source_signature,
    _parse_l4_install_commands,
    _prepare_l4_workspace,
    _verify_l4_source_unchanged,
)


def _make_run_worktree(root: Path) -> Path:
    wt = root / "run-worktree"
    wt.mkdir()
    (wt / "RUN.md").write_text(
        "# Run\n\n"
        "Install via pip: `pip install imaginary`\n\n"
        "```bash\n"
        "$ python -m venv .venv\n"
        "$ .venv/bin/pip install -r requirements.txt\n"
        "uvicorn app.main:app --port 8000\n"
        "```\n"
    )
    (wt / "requirements.txt").write_text("\n")
    app = wt / "app"
    app.mkdir()
    (app / "main.py").write_text("print('ok')\n")
    return wt


def test_install_parser_accepts_only_shell_install_commands(tmp_path: Path):
    wt = _make_run_worktree(tmp_path)
    commands = _parse_l4_install_commands(wt / "RUN.md")

    assert "python -m venv .venv" in commands
    assert ".venv/bin/pip install -r requirements.txt" in commands
    assert all("imaginary" not in command for command in commands)
    assert all("uvicorn" not in command for command in commands)


def test_is_install_command_rejects_prose():
    assert _is_install_command("pip install -r requirements.txt")
    assert not _is_install_command("Install via pip: `pip install acme`")


def test_prepare_l4_workspace_copies_scopes_and_freezes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    wt = _make_run_worktree(tmp_path)
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path / "workspace"))

    dst, install_logs, baseline = _prepare_l4_workspace("run_123", str(wt), install_timeout_s=60)
    try:
        assert dst == tmp_path / "workspace" / "l4_runs" / "run_123"
        assert (dst / "RUN.md").exists()
        assert (dst / "l4_scratch").is_dir()
        assert install_logs
        assert "app/main.py" in baseline

        config = json.loads((dst / "opencode.json").read_text())
        assert config["permission"]["edit"]["*"] == "deny"
        assert config["permission"]["edit"]["l4_scratch/**"] == "allow"
        assert config["permission"]["webfetch"] == "deny"
        assert config["permission"]["bash"]["git *"] == "deny"

        assert not os.access(dst / "app" / "main.py", os.W_OK)
        assert os.access(dst / "l4_scratch", os.W_OK)
    finally:
        _cleanup_l4_workspace(dst)

    assert not dst.exists()


def test_verify_l4_source_unchanged_detects_mutation(tmp_path: Path):
    wt = _make_run_worktree(tmp_path)
    baseline = _l4_source_signature(wt)
    (wt / "app" / "main.py").write_text("print('mutated')\n")

    with pytest.raises(RuntimeError, match="L4 source changed"):
        _verify_l4_source_unchanged(wt, baseline)
