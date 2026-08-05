"""conductor_variants File 03 E2E — the full path against the REAL scaffold.

Drives the actual ``scaffolds_store/design-layout-v2`` (real check_tokens.py,
gates.sh, curated variants) through ``generate_workspace`` to prove the 03.5
scenarios: selection → generation → gate fires/passes → extension legal →
bypass caught → handoff → second-goal pin reuse → library isolation →
no-variant standards unaffected.

Uses a disposable copy of the scaffold so the real library is never mutated.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

import os  # noqa: E402

os.environ.setdefault("DATABASE_URL", "postgresql://test:test@127.0.0.1:5432/test")

from backend.planning import harness_worktree as hw  # noqa: E402
from backend.planning.meta_planner import goal_formulator as gf  # noqa: E402

REAL_SCAFFOLD = Path("/opt/aipc/conductor/scaffolds_store/design-layout-v2")


@pytest.fixture()
def scaffold(tmp_path):
    """Disposable copy of the real design-layout-v2 scaffold."""
    src = tmp_path / "scaffold"
    shutil.copytree(REAL_SCAFFOLD, src, ignore=shutil.ignore_patterns("__pycache__"))
    return src


def _real_std(scaffold: Path) -> dict:
    variants = {
        d.name: {"dir": f"variants/{d.name}", "blurb": ""}
        for d in (scaffold / "variants").iterdir()
        if d.is_dir()
    }
    return {
        "id": "std-design",
        "slug": "design-layout-v2",
        "name": "Design Layout v2",
        "conventions_md": "# Design Layout Conventions (v2 — variant system)",
        "scaffold_ref": str(scaffold),
        "version": 6,
        "artifact_spec": {"delivery_spec": {"form": "viewable_artifacts"}},
        "service_template": {},
        "variants": variants,
    }


def _repo(tmp_path, name="proj") -> Path:
    proj = tmp_path / name
    proj.mkdir()
    subprocess.run(["git", "-C", str(proj), "init"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(proj), "config", "user.email", "t@t"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(proj), "config", "user.name", "t"], check=True, capture_output=True)
    (proj / "README.md").write_text(f"# {name}\n")
    subprocess.run(["git", "-C", str(proj), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(proj), "commit", "-m", "init"], check=True, capture_output=True)
    return proj


def _run_gates(workdir: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "gates.sh"], cwd=str(workdir), capture_output=True, text=True, timeout=120,
    )


def _freeze_export(design: Path) -> None:
    (design / "exports").mkdir(parents=True, exist_ok=True)
    src = design / "work" / "reference.html"
    if src.exists():
        (design / "exports" / (src.stem + ".html")).write_bytes(
            src.read_bytes(),
        )


# ── 03.5: selection ─────────────────────────────────────────────────────────

class TestSelection:
    def test_editorial_goal_picks_editorial_serif(self, scaffold, monkeypatch):
        monkeypatch.setattr(
            gf, "call_llm_structured",
            lambda prompt, schema: gf.VariantChoice(variant="editorial-serif", rationale="editorial tone"),
        )
        chosen = gf.select_variant(
            "landing page for a used-car marketplace, editorial tone",
            "editorial long-form",
            _real_std(scaffold),
        )
        assert chosen == "editorial-serif"

    def test_manifest_pin_reused_no_repick(self, tmp_path, scaffold, monkeypatch):
        monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
        proj = tmp_path / "pin"
        (proj / ".conductor").mkdir(parents=True)
        (proj / ".conductor" / "workspace.json").write_text(json.dumps({
            "layout": "subdirs",
            "components": [{"standard_slug": "design-layout-v2", "subdir": "design", "variant": "editorial-serif"}],
        }), encoding="utf-8")
        monkeypatch.setattr(
            gf, "call_llm_structured",
            lambda prompt, schema: (_ for _ in ()).throw(AssertionError("must not re-pick")),
        )
        comp = gf.Component(standard_slug="design-layout-v2", subdir="design")
        gf._pin_variants([comp], "a totally different goal", "spec", project_id="pin")
        assert comp.variant == "editorial-serif"


# ── 03.5: generation + gate lifecycle against the real scaffold ────────────

class TestGenerationAndGate:
    def test_seeds_variant_and_gate_passes(self, tmp_path, scaffold, monkeypatch):
        monkeypatch.setattr(hw, "_get_active_standard", lambda s: _real_std(scaffold))
        proj = _repo(tmp_path, "gen")
        hw.generate_workspace(proj, "design-layout-v2", subdir="design", variant="editorial-serif", defer_commit=True)

        design = proj / "design"
        assert (design / "DESIGN.md").exists()
        assert (design / "work" / "tokens.css").exists()
        assert (design / "work" / "reference.html").exists()
        manifest = json.loads((design / ".conductor" / "workspace.json").read_text())
        assert manifest["variant"] == "editorial-serif"

        _freeze_export(design)
        result = _run_gates(design)
        assert "ALL GATES GREEN" in result.stdout, result.stdout + result.stderr

    def test_gate_fires_on_raw_hex_and_recovers(self, tmp_path, scaffold, monkeypatch):
        monkeypatch.setattr(hw, "_get_active_standard", lambda s: _real_std(scaffold))
        proj = _repo(tmp_path, "fire")
        hw.generate_workspace(proj, "design-layout-v2", subdir="design", variant="mono", defer_commit=True)
        design = proj / "design"

        (design / "work" / "page.css").write_text(
            ".hero { color: #ff0000; }\n", encoding="utf-8",
        )
        failed = _run_gates(design)
        assert failed.returncode != 0
        assert "raw color" in failed.stdout + failed.stderr

        (design / "work" / "page.css").write_text(
            ".hero { color: var(--fg); }\n", encoding="utf-8",
        )
        _freeze_export(design)
        passed = _run_gates(design)
        assert "ALL GATES GREEN" in passed.stdout

    def test_extension_with_new_token_is_legal(self, tmp_path, scaffold, monkeypatch):
        monkeypatch.setattr(hw, "_get_active_standard", lambda s: _real_std(scaffold))
        proj = _repo(tmp_path, "ext")
        hw.generate_workspace(proj, "design-layout-v2", subdir="design", variant="soft-clay", defer_commit=True)
        design = proj / "design"

        with (design / "work" / "tokens.css").open("a", encoding="utf-8") as fh:
            fh.write(":root { --accent-2: #7a9e4b; }\n")
        (design / "work" / "page.css").write_text(
            ".btn { background: var(--accent-2); }\n", encoding="utf-8",
        )
        _freeze_export(design)
        result = _run_gates(design)
        assert "ALL GATES GREEN" in result.stdout, result.stdout + result.stderr

    def test_bypass_caught_by_tokens_used(self, tmp_path, scaffold, monkeypatch):
        monkeypatch.setattr(hw, "_get_active_standard", lambda s: _real_std(scaffold))
        proj = _repo(tmp_path, "bypass")
        hw.generate_workspace(proj, "design-layout-v2", subdir="design", variant="technical-dense", defer_commit=True)
        design = proj / "design"

        (design / "work" / "rogue.css").write_text(
            ".x { margin: 0; padding: 0; }\n", encoding="utf-8",
        )
        result = _run_gates(design)
        assert result.returncode != 0
        assert "tokens_used" in result.stdout + result.stderr


# ── 03.5: handoff (real design dep → frontend copy + sha) ──────────────────

class TestHandoffE2E:
    def test_tokens_copied_and_sha_recorded_then_frontend_gate_passes(
        self, tmp_path, scaffold, monkeypatch,
    ):
        monkeypatch.setattr(hw, "_get_active_standard", lambda s: _real_std(scaffold))
        import shutil as _sh
        fe = tmp_path / "fe_scaffold"
        _sh.copytree(Path("/opt/aipc/conductor/scaffolds_store/react-frontend-v1"), fe,
                     ignore=shutil.ignore_patterns("__pycache__"))
        monkeypatch.setattr(
            hw, "_get_active_standard",
            lambda s: _real_std(scaffold) if s == "design-layout-v2" else {
                "id": "std-fe", "slug": "react-frontend", "name": "React Frontend",
                "conventions_md": "# React Frontend Conventions", "scaffold_ref": str(fe),
                "version": 1, "artifact_spec": {}, "service_template": {}, "variants": {},
            },
        )

        proj = _repo(tmp_path, "handoff")
        hw.generate_workspace(proj, "design-layout-v2", subdir="design", variant="brutalism", defer_commit=True)
        hw.generate_workspace(proj, "react-frontend", subdir="frontend", defer_commit=True)

        manifest = {
            "layout": "subdirs",
            "components": [
                {"standard_slug": "design-layout-v2", "subdir": "design", "variant": "brutalism"},
                {"standard_slug": "react-frontend", "subdir": "frontend"},
            ],
        }
        (proj / ".conductor").mkdir(parents=True, exist_ok=True)
        (proj / ".conductor" / "workspace.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8",
        )

        materialized = [{"dep_name": "design", "dep_project_id": "proj-design", "path": str(proj / "design")}]
        entry = hw.handoff_design_tokens(proj, proj, materialized, dep_shas={"proj-design": "sha-123"})

        assert entry is not None
        assert entry["token_source"]["sha"] == "sha-123"
        styles = proj / "frontend" / "src" / "styles" / "tokens.css"
        assert styles.exists()
        assert (proj / "frontend" / "scripts" / "check_tokens.py").exists()

        (proj / "frontend" / "src" / "App.tsx").write_text(
            "export const App = () => <div style={{ color: 'var(--fg)' }}>ok</div>;\n",
            encoding="utf-8",
        )
        fe_gates = proj / "frontend" / "gates.sh"
        assert fe_gates.exists()
        token_gate = subprocess.run(
            ["python3", str(proj / "frontend" / "scripts" / "check_tokens.py"), str(proj / "frontend")],
            capture_output=True, text=True, timeout=120,
        )
        assert token_gate.returncode == 0, token_gate.stdout + token_gate.stderr


# ── 03.5: library isolation + no-variant standards ─────────────────────────

class TestIsolation:
    def test_library_edit_does_not_reach_finished_project(self, tmp_path, scaffold, monkeypatch):
        monkeypatch.setattr(hw, "_get_active_standard", lambda s: _real_std(scaffold))
        proj = _repo(tmp_path, "iso")
        hw.generate_workspace(proj, "design-layout-v2", subdir="design", variant="brutalism", defer_commit=True)
        seeded = (proj / "design" / "work" / "tokens.css").read_text()

        lib_tokens = scaffold / "variants" / "brutalism" / "tokens.css"
        lib_tokens.write_text(lib_tokens.read_text() + "\n:root { --hacked: #000; }\n", encoding="utf-8")

        assert (proj / "design" / "work" / "tokens.css").read_text() == seeded
        _freeze_export(proj / "design")
        assert "ALL GATES GREEN" in _run_gates(proj / "design").stdout

    def test_no_variant_standard_unaffected(self, tmp_path, scaffold, monkeypatch):
        from backend.tests.test_variants import _make_scaffold as _mk
        src = _mk(tmp_path / "be")
        proj = _repo(tmp_path, "backend")
        monkeypatch.setattr(hw, "_get_active_standard", lambda s: dict(
            {"id": "std-be", "slug": "python-backend", "name": "Python Backend",
             "conventions_md": "# Python Backend Conventions", "scaffold_ref": str(src),
             "version": 1, "artifact_spec": {}, "service_template": {}, "variants": {}},
        ))
        m = hw.generate_workspace(proj, "python-backend", subdir="api", defer_commit=True)
        assert "variant" not in m
        assert (proj / "api" / "AGENTS.md").exists()
