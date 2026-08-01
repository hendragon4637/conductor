"""Tests for File 03 workspace manifest (T-03.3/03.4/03.8).

Covers: full L4 component entries (delivery_form, runnable, port, health,
env, commands), token substitution invariants, version-pin policy, manifest
backfill, and the RUN.md drift gate.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql://test:test@127.0.0.1:5432/test")

from backend.planning import harness_worktree as hw  # noqa: E402


_STD_ROWS = {
    "react-frontend": {
        "id": "std-react",
        "slug": "react-frontend",
        "name": "React Frontend",
        "conventions_md": "# React",
        "scaffold_ref": "/scaffolds/react-frontend",
        "version": 7,
        "artifact_spec": {"delivery_spec": {"form": "served_url"}},
        "service_template": {
            "runnable": True,
            "port": 5173,
            "health": "/",
            "env_required": [],
            "env_l4_defaults": {"VITE_API_URL": "http://localhost:8000"},
            "commands": {
                "setup": "npm install",
                "run": "npm run dev",
                "test": "npm test",
                "verify": "npm run build",
            },
        },
    },
    "python-backend": {
        "id": "std-python",
        "slug": "python-backend",
        "name": "Python Backend",
        "conventions_md": "# Python",
        "scaffold_ref": "/scaffolds/python-backend",
        "version": 7,
        "artifact_spec": {"delivery_spec": {"form": "served_url"}},
        "service_template": {
            "runnable": True,
            "port": 8000,
            "health": "/health",
            "env_required": ["DATABASE_URL"],
            "env_l4_defaults": {"LOG_LEVEL": "info"},
            "commands": {
                "setup": "uv sync",
                "run": "uv run uvicorn __PKG__.main:app --reload --port ${PORT}",
                "test": "uv run pytest",
                "verify": "uv run ruff check .",
            },
        },
    },
    "arduino": {
        "id": "std-arduino",
        "slug": "arduino",
        "name": "Arduino Firmware",
        "conventions_md": "# Arduino",
        "scaffold_ref": "/scaffolds/arduino",
        "version": 5,
        "artifact_spec": {"delivery_spec": {"form": "firmware"}},
        "service_template": {
            "runnable": False,
            "commands": {
                "setup": "pio project config",
                "run": "pio run -e uno",
                "test": "pio test -e uno",
            },
        },
    },
    "tech-docs": {
        "id": "std-docs",
        "slug": "tech-docs",
        "name": "Technical Docs",
        "conventions_md": "# Docs",
        "scaffold_ref": "/scaffolds/tech-docs",
        "version": 4,
        "artifact_spec": {"delivery_spec": {"form": "markdown"}},
        "service_template": {},
    },
}


@pytest.fixture(autouse=True)
def _mock_db(monkeypatch):
    """Patch backend.db.queries.psycopg.connect with canned domain_standards."""
    import backend.db.queries as q

    class FakeCursor:
        def __init__(self):
            self.last_params = ()

        def execute(self, sql, params=None):
            self.last_params = params or ()
            return self

        def fetchone(self):
            if not self.last_params:
                return None
            key = str(self.last_params[0])
            for _std in _STD_ROWS.values():
                if key in (_std["id"], _std["slug"]):
                    return dict(_std)
            return None

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class FakeConn:
        def __init__(self):
            self.cursor_obj = FakeCursor()

        def execute(self, sql, params=None):
            return self.cursor_obj.execute(sql, params)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    fake = FakeConn()
    monkeypatch.setattr(q.psycopg, "connect", lambda *a, **k: fake)
    return fake


def _write_manifest(project: Path, components: list[dict]) -> None:
    (project / ".conductor").mkdir(parents=True, exist_ok=True)
    (project / ".conductor" / "workspace.json").write_text(
        json.dumps({"layout": "subdirs", "components": components}, indent=2) + "\n",
        encoding="utf-8",
    )


class TestWriteProjectManifestL4Fields:
    def test_entries_carry_full_l4_fields(self, tmp_path):
        manifest = hw.write_project_manifest(
            tmp_path / "my_app",
            [{"standard_slug": "react-frontend", "subdir": "frontend"}],
        )
        comp = manifest["components"][0]
        assert manifest["layout"] == "subdirs"
        assert comp["subdir"] == "frontend"
        assert comp["standard_slug"] == "react-frontend"
        assert comp["standard_id"] == "std-react"
        assert comp["version"] == 7
        assert comp["domain"] == "react-frontend"
        assert comp["delivery_form"] == "served_url"
        assert comp["runnable"] is True
        assert comp["port"] == 5173
        assert comp["health"] == "/"
        assert comp["env_l4_defaults"] == {"VITE_API_URL": "http://localhost:8000"}
        assert comp["commands"] == {
            "setup": "npm install",
            "run": "npm run dev",
            "test": "npm test",
            "verify": "npm run build",
        }

    def test_commands_substitute_generation_tokens_only(self, tmp_path):
        manifest = hw.write_project_manifest(
            tmp_path / "my-app",
            [{"standard_slug": "python-backend", "subdir": "backend"}],
        )
        run = manifest["components"][0]["commands"]["run"]
        assert run == "uv run uvicorn my_app.main:app --reload --port ${PORT}"
        assert "${PORT}" in run  # runtime token survives untouched

    def test_no_service_template_degrades_gracefully(self, tmp_path):
        manifest = hw.write_project_manifest(
            tmp_path / "my_app",
            [{"standard_slug": "tech-docs", "subdir": "docs"}],
        )
        comp = manifest["components"][0]
        assert comp["runnable"] is False
        assert comp["commands"] == {}
        assert comp["port"] is None
        assert comp["delivery_form"] == "markdown"

    def test_layout_root_and_mixed(self, tmp_path):
        root = hw.write_project_manifest(tmp_path / "a", [{"standard_slug": "tech-docs", "subdir": "."}])
        assert root["layout"] == "root"

        mixed = hw.write_project_manifest(
            tmp_path / "b",
            [
                {"standard_slug": "tech-docs", "subdir": "."},
                {"standard_slug": "react-frontend", "subdir": "frontend"},
            ],
        )
        assert mixed["layout"] == "mixed"


class TestVersionPinPolicy:
    def test_existing_subdir_keeps_pinned_version(self, tmp_path):
        project = tmp_path / "my_app"
        _write_manifest(
            project,
            [{
                "standard_slug": "react-frontend",
                "standard_id": "std-react",
                "version": 3,
                "subdir": "frontend",
                "domain": "react-frontend",
            }],
        )
        manifest = hw.write_project_manifest(
            project,
            [{"standard_slug": "react-frontend", "subdir": "frontend"}],
        )
        comp = manifest["components"][0]
        assert comp["version"] == 3  # pinned, not bumped to 7
        assert comp["commands"]["run"] == "npm run dev"  # L4 fields filled


class TestBackfillManifest:
    def test_fills_missing_commands_preserving_version(self, tmp_path):
        project = tmp_path / "my_app"
        _write_manifest(
            project,
            [{
                "standard_slug": "react-frontend",
                "standard_id": "std-react",
                "version": 3,
                "subdir": "frontend",
                "domain": "react-frontend",
            }],
        )
        manifest = hw.backfill_manifest(project, defer_commit=True)
        comp = manifest["components"][0]
        assert comp["version"] == 3
        assert comp["delivery_form"] == "served_url"
        assert comp["runnable"] is True
        assert comp["port"] == 5173
        assert comp["commands"]["run"] == "npm run dev"

    def test_idempotent(self, tmp_path):
        project = tmp_path / "my_app"
        _write_manifest(
            project,
            [{
                "standard_slug": "react-frontend",
                "standard_id": "std-react",
                "version": 7,
                "subdir": "frontend",
                "domain": "react-frontend",
            }],
        )
        first = hw.backfill_manifest(project, defer_commit=True)
        second = hw.backfill_manifest(project, defer_commit=True)
        assert first == second

    def test_no_manifest_returns_none(self, tmp_path):
        assert hw.backfill_manifest(tmp_path / "nope", defer_commit=True) is None


class TestRunmdDrift:
    _COMP = {
        "standard_slug": "cli-tool",
        "standard_id": "std-cli",
        "version": 4,
        "subdir": "tools",
        "delivery_form": "installed_command",
        "runnable": True,
        "port": None,
        "health": None,
        "env_required": [],
        "env_l4_defaults": {},
        "commands": {
            "setup": "uv sync",
            "run": "uv run cli shout hello",
            "test": "pytest",
            "verify": "ruff check .",
        },
    }

    def test_no_drift_when_runmd_documents_commands(self, tmp_path):
        project = tmp_path / "my_app"
        _write_manifest(project, [dict(self._COMP)])
        (project / "tools").mkdir(parents=True)
        (project / "tools" / "RUN.md").write_text(
            "# Setup\nuv sync\n# Run\nuv run cli shout hello\n# Test\npytest\n# Verify\nruff check .\n",
            encoding="utf-8",
        )
        assert hw.check_runmd_drift(project, "tools") == []

    def test_drift_detects_missing_command_token(self, tmp_path):
        project = tmp_path / "my_app"
        _write_manifest(project, [dict(self._COMP)])
        (project / "tools").mkdir(parents=True)
        (project / "tools" / "RUN.md").write_text(
            "# Run\nuv run cli shout hello\n", encoding="utf-8"
        )
        assert hw.check_runmd_drift(project, "tools") == ["test", "verify"]

    def test_missing_runmd_flags_all_commands(self, tmp_path):
        project = tmp_path / "my_app"
        _write_manifest(project, [dict(self._COMP)])
        (project / "tools").mkdir(parents=True)
        assert hw.check_runmd_drift(project, "tools") == ["setup", "run", "test", "verify"]

    def test_unknown_subdir_reports_no_drift(self, tmp_path):
        project = tmp_path / "my_app"
        _write_manifest(project, [dict(self._COMP)])
        assert hw.check_runmd_drift(project, "missing") == []


class TestRunmdDriftGateEmission:
    def test_root_gates_emits_drift_gate_for_standard_bearing(self, tmp_path):
        project = tmp_path / "my_app"
        (project / "backend").mkdir(parents=True)
        (project / "backend" / "gates.sh").write_text("#!/usr/bin/env bash\nexit 0\n")
        manifest = hw.write_project_manifest(
            project, [{"standard_slug": "python-backend", "subdir": "backend"}]
        )
        hw.write_root_gates(project, manifest)
        gates = (project / "gates.sh").read_text(encoding="utf-8")
        assert "RUN.md drift gate" in gates
        assert "python3 - <<'PY'" in gates

    def test_root_gates_skips_drift_gate_without_service_template(self, tmp_path):
        project = tmp_path / "my_app"
        (project / "docs").mkdir(parents=True)
        (project / "docs" / "gates.sh").write_text("#!/usr/bin/env bash\nexit 0\n")
        manifest = hw.write_project_manifest(
            project, [{"standard_slug": "tech-docs", "subdir": "docs"}]
        )
        hw.write_root_gates(project, manifest)
        gates = (project / "gates.sh").read_text(encoding="utf-8")
        assert "RUN.md drift gate" not in gates

    def test_gates_sh_fails_readably_on_drift(self, tmp_path):
        project = tmp_path / "my_app"
        _write_manifest(project, [dict(TestRunmdDrift._COMP)])
        (project / "tools").mkdir(parents=True)
        (project / "tools" / "gates.sh").write_text("#!/usr/bin/env bash\nexit 0\n")
        (project / "tools" / "RUN.md").write_text(
            "# Run\nuv run cli shout hello\n", encoding="utf-8"
        )
        manifest = {"layout": "subdirs", "components": [dict(TestRunmdDrift._COMP)]}
        hw.write_root_gates(project, manifest)
        proc = subprocess.run(
            ["bash", "gates.sh"], cwd=project, capture_output=True, text=True, timeout=30
        )
        assert proc.returncode != 0
        assert "RUN.md drift" in proc.stdout + proc.stderr

    def test_gates_sh_passes_when_runmd_documents(self, tmp_path):
        project = tmp_path / "my_app"
        _write_manifest(project, [dict(TestRunmdDrift._COMP)])
        (project / "tools").mkdir(parents=True)
        (project / "tools" / "gates.sh").write_text("#!/usr/bin/env bash\nexit 0\n")
        (project / "tools" / "RUN.md").write_text(
            "# Setup\nuv sync\n# Run\nuv run cli shout hello\n# Test\npytest\n# Verify\nruff check .\n",
            encoding="utf-8",
        )
        manifest = {"layout": "subdirs", "components": [dict(TestRunmdDrift._COMP)]}
        hw.write_root_gates(project, manifest)
        proc = subprocess.run(
            ["bash", "gates.sh"], cwd=project, capture_output=True, text=True, timeout=30
        )
        assert proc.returncode == 0


# ── seed shape contract (T-11.2a regression, guide 01.8/01.10) ───────────────


class TestServiceTemplateShapeContract:
    """Every seeded SERVICE_TEMPLATES entry conforms to the guide 01.8 shape."""

    def test_every_template_has_commands_setup_and_verify(self):
        from backend.standards.seeder import SERVICE_TEMPLATES

        assert SERVICE_TEMPLATES  # non-empty guard
        for slug, tpl in SERVICE_TEMPLATES.items():
            cmds = tpl["commands"]
            assert isinstance(cmds, dict), slug
            assert "setup" in cmds and "verify" in cmds, slug

    def test_runnable_templates_carry_run_port_health_env(self):
        from backend.standards.seeder import SERVICE_TEMPLATES

        for slug, tpl in SERVICE_TEMPLATES.items():
            if not tpl.get("runnable"):
                continue
            assert isinstance(tpl["port"], int), slug
            assert isinstance(tpl["health"], str), slug
            assert isinstance(tpl["env_required"], list), slug
            assert isinstance(tpl["env_l4_defaults"], dict), slug
            assert "run" in tpl["commands"], slug

    def test_all_templates_have_env_keys(self):
        from backend.standards.seeder import SERVICE_TEMPLATES

        for slug, tpl in SERVICE_TEMPLATES.items():
            assert "env_required" in tpl, slug
            assert "env_l4_defaults" in tpl, slug

    def test_only_python_backend_declares_env_required(self):
        from backend.standards.seeder import SERVICE_TEMPLATES

        for slug, tpl in SERVICE_TEMPLATES.items():
            declared = set(tpl["env_required"])
            if slug == "python-backend":
                assert declared == {"DATABASE_URL"}
            else:
                assert declared == set(), slug

    def test_every_template_has_a_publish_manifest(self):
        from backend.standards.seeder import PUBLISH_MANIFESTS, SERVICE_TEMPLATES

        assert set(PUBLISH_MANIFESTS) == set(SERVICE_TEMPLATES)

    def test_every_publish_manifest_carries_declared_files(self):
        from backend.standards.seeder import PUBLISH_MANIFESTS

        for slug, manifest in PUBLISH_MANIFESTS.items():
            assert isinstance(manifest["image_tag"], bool), slug
            assert isinstance(manifest["files"], list), slug
            assert isinstance(manifest["artifacts"], list), slug
            assert ".conductor/workspace.json" in manifest["files"], slug
            assert all(isinstance(f, str) and f for f in manifest["files"]), slug


# ── emit_workspace_picture() — deps/ excluded ────────────────────────────────

class TestWorkspacePictureDepsExcluded:
    """Guide 09.4: deps/ must never be inlined into WORKSPACE.md."""

    def test_tree_excludes_deps_dir(self, tmp_path):
        project = tmp_path / "my_app"
        (project / "src").mkdir(parents=True)
        (project / "src" / "main.py").write_text("x = 1\n")
        (project / "deps" / "backend" / "src").mkdir(parents=True)
        (project / "deps" / "backend" / "src" / "server.py").write_text("y = 2\n")

        hw.emit_workspace_picture(project)
        out = (project / ".plan" / "research" / "WORKSPACE.md").read_text(encoding="utf-8")
        assert "src/main.py" in out
        assert "deps" not in out

    def test_manifest_sections_exclude_deps(self, tmp_path):
        project = tmp_path / "my_app"
        (project / "src").mkdir(parents=True)
        (project / "src" / "main.py").write_text("x = 1\n")
        (project / "deps" / "backend").mkdir(parents=True)
        (project / "deps" / "backend" / "package.json").write_text("{\"name\": \"backend\"}\n")

        hw.emit_workspace_picture(project)
        out = (project / ".plan" / "research" / "WORKSPACE.md").read_text(encoding="utf-8")
        assert "package.json" not in out
