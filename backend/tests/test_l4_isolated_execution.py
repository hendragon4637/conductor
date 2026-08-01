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
    _l4_source_signature,
    _manifest_install_blocks,
    _prepare_l4_workspace,
    _run_l4_install_blocks,
    _verify_l4_source_unchanged,
)
from services.evaluator.l4_runner import (
    _verify_l4_source_baseline,
    _write_worksystem_opencode_json,
)


def _make_run_worktree(root: Path) -> Path:
    wt = root / "run-worktree"
    wt.mkdir()
    (wt / "RUN.md").write_text(
        "# Run\n\n"
        "Install via pip: `pip install imaginary`\n\n"
        "```bash\n"
        "python -m venv .venv\n"
        ".venv/bin/pip install -r requirements.txt\n"
        "uvicorn app.main:app --port 8000\n"
        "```\n"
    )
    (wt / "requirements.txt").write_text("\n")
    app = wt / "app"
    app.mkdir()
    (app / "main.py").write_text("print('ok')\n")
    return wt


def _write_manifest(wt: Path) -> None:
    (wt / ".conductor").mkdir()
    (wt / ".conductor" / "workspace.json").write_text(
        json.dumps({
            "components": [
                {
                    "subdir": ".",
                    "standard_slug": "python-backend",
                    "commands": {
                        "setup": "touch setup-ran.txt",
                        "run": "uvicorn app.main:app",
                    },
                },
                {
                    "subdir": "react-frontend",
                    "standard_slug": "react-frontend",
                    "commands": {"run": "npm run dev"},
                },
            ]
        })
    )


def test_manifest_install_blocks_extracts_setup_per_component(tmp_path: Path):
    wt = _make_run_worktree(tmp_path)
    _write_manifest(wt)

    blocks = _manifest_install_blocks(wt)

    assert [b[0] for b in blocks] == ["."]
    assert blocks[0][1] == "touch setup-ran.txt"


def test_manifest_install_blocks_returns_empty_without_manifest(tmp_path: Path):
    wt = _make_run_worktree(tmp_path)
    assert _manifest_install_blocks(wt) == []


def test_manifest_install_blocks_skips_components_without_setup(tmp_path: Path):
    wt = _make_run_worktree(tmp_path)
    _write_manifest(wt)

    blocks = _manifest_install_blocks(wt)

    assert all("react-frontend" != b[0] for b in blocks)


def test_manifest_install_blocks_tolerates_unparseable_manifest(tmp_path: Path):
    wt = _make_run_worktree(tmp_path)
    (wt / ".conductor").mkdir()
    (wt / ".conductor" / "workspace.json").write_text("{not json")

    assert _manifest_install_blocks(wt) == []


def test_run_l4_install_blocks_uses_component_subdir_cwd(tmp_path: Path):
    dst = tmp_path / "dst"
    dst.mkdir()
    (dst / "react-frontend").mkdir()

    logs = _run_l4_install_blocks(
        dst, [("react-frontend", "touch setup-ran.txt")], timeout_s=30
    )

    assert (dst / "react-frontend" / "setup-ran.txt").exists()
    assert not (dst / "setup-ran.txt").exists()
    assert "exit 0" in logs[0]


def test_run_l4_install_blocks_raises_on_nonzero(tmp_path: Path):
    dst = tmp_path / "dst"
    dst.mkdir()

    with pytest.raises(RuntimeError, match="L4 setup failed"):
        _run_l4_install_blocks(dst, [(".", "exit 3")], timeout_s=30)


def test_prepare_l4_workspace_copies_scopes_and_freezes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    wt = _make_run_worktree(tmp_path)
    _write_manifest(wt)
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path / "workspace"))

    dst, install_logs, baseline = _prepare_l4_workspace("run_123", str(wt), install_timeout_s=60)
    try:
        assert dst == tmp_path / "workspace" / "l4_runs" / "run_123"
        assert (dst / "RUN.md").exists()
        assert (dst / "l4_scratch").is_dir()
        assert install_logs == ["[.] touch setup-ran.txt -> exit 0"]
        assert (dst / "setup-ran.txt").exists()
        assert "app/main.py" in baseline

        persisted = json.loads((dst / "l4_scratch" / "source_baseline.json").read_text())
        assert persisted == baseline

        config = json.loads((dst / "opencode.json").read_text())
        assert config["permission"]["edit"]["*"] == "allow"
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


def test_verify_l4_source_baseline_ok_and_fail_open(tmp_path: Path):
    wt = _make_run_worktree(tmp_path)
    (wt / "l4_scratch").mkdir()
    (wt / "l4_scratch" / "source_baseline.json").write_text(
        json.dumps(_l4_source_signature(wt))
    )

    assert _verify_l4_source_baseline(wt)
    assert _verify_l4_source_baseline(tmp_path / "no-baseline-worktree")


def test_verify_l4_source_baseline_detects_mutation(tmp_path: Path):
    wt = _make_run_worktree(tmp_path)
    (wt / "l4_scratch").mkdir()
    (wt / "l4_scratch" / "source_baseline.json").write_text(
        json.dumps(_l4_source_signature(wt))
    )
    (wt / "app" / "main.py").write_text("print('mutated')\n")

    assert not _verify_l4_source_baseline(wt)


# ── worksystem snapshot sandbox (T-11.2c, guide 10.5) ───────────────────────


def test_worksystem_opencode_json_allows_edits_denies_git_and_web(tmp_path: Path):
    wt = tmp_path / "snapshot-wt"
    wt.mkdir()
    _write_worksystem_opencode_json(wt)

    config = json.loads((wt / "opencode.json").read_text())
    perm = config["permission"]
    assert perm["edit"]["*"] == "allow"
    assert perm["webfetch"] == "deny"
    assert perm["websearch"] == "deny"
    for denied in ("git *", "sudo *", "rm -rf *"):
        assert perm["bash"][denied] == "deny"
    assert perm["bash"]["*"] == "allow"


# ── RUN.md-is-never-parsed invariant (T-11.3, guide 05.8) ────────────────────


def test_runmd_install_instructions_never_become_blocks(tmp_path: Path):
    """L4 install commands come from the manifest only, never from RUN.md."""
    wt = _make_run_worktree(tmp_path)
    _write_manifest(wt)
    assert "pip install" in (wt / "RUN.md").read_text()

    blocks = _manifest_install_blocks(wt)

    assert [b[0] for b in blocks] == ["."]
    assert blocks[0][1] == "touch setup-ran.txt"
    assert all("pip" not in cmd for _, cmd in blocks)
