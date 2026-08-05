"""Tests for conductor_variants File 02 — selection, manifest pin, generation seed.

Covers guide 02.6 (select_variant precedence: no-variants > single > LLM pick,
manifest pin + reuse on later goals) and guide 02.7 (generate_workspace seeds
the variant's DESIGN.md/tokens.css/reference.html, unknown variant rejected,
never-overwrite protected at the caller's existing-subdir skip).
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql://test:test@127.0.0.1:5432/test")

from backend.planning.meta_planner import goal_formulator as gf  # noqa: E402
from backend.planning import harness_worktree as hw  # noqa: E402


_DESIGN_VARIANTS = {
    "technical-dense": {"dir": "variants/technical-dense", "blurb": "dense data-rich. Not for editorial."},
    "editorial-serif": {"dir": "variants/editorial-serif", "blurb": "long-form serif. Not for dashboards."},
    "soft-clay": {"dir": "variants/soft-clay", "blurb": "friendly rounded. Not for brutalist."},
    "brutalism": {"dir": "variants/brutalism", "blurb": "bold concrete. Not for soft clay."},
    "mono": {"dir": "variants/mono", "blurb": "monospace matrix. Not for prose."},
}

_STD_ROWS = {
    "design-layout-v2": {
        "id": "std-design",
        "slug": "design-layout-v2",
        "name": "Design Layout v2",
        "conventions_md": "# Design Layout Conventions (v2 — variant system)",
        "scaffold_ref": "/scaffolds/design-layout-v2",
        "version": 6,
        "artifact_spec": {"delivery_spec": {"form": "viewable_artifacts"}},
        "service_template": {},
        "variants": _DESIGN_VARIANTS,
    },
    "single-variant": {
        "id": "std-single",
        "slug": "single-variant",
        "name": "Single Variant",
        "conventions_md": "# Single",
        "scaffold_ref": "/scaffolds/single-variant",
        "version": 1,
        "artifact_spec": {"delivery_spec": {"form": "viewable_artifacts"}},
        "service_template": {},
        "variants": {"mono": {"dir": "variants/mono", "blurb": "only option"}},
    },
    "strong-oracle": {
        "id": "std-oracle",
        "slug": "strong-oracle",
        "name": "Strong Oracle",
        "conventions_md": "# Oracle",
        "scaffold_ref": "/scaffolds/strong-oracle",
        "version": 1,
        "artifact_spec": {"delivery_spec": {"form": "served_url"}},
        "service_template": {"runnable": True, "commands": {}},
        "variants": {},
    },
}

# loader.get_standard returns a positional tuple row
_LOADER_TUPLE = {
    "design-layout-v2": (
        "design-layout-v2", "Design Layout v2", "domain",
        "# Design Layout Conventions (v2 — variant system)", None, {},
        [], None, 6, ["design", "creative"], _DESIGN_VARIANTS,
    ),
    "strong-oracle": (
        "strong-oracle", "Strong Oracle", "domain",
        "# Oracle", None, {}, [], None, 1, [], {},
    ),
}


def _make_choice(variant: str) -> "gf.VariantChoice":
    return gf.VariantChoice(variant=variant, rationale="ok")


class _FakeCursor:
    def __init__(self, rows: dict):
        self.last_params = ()
        self.rows = rows
        self.sql = ""

    def execute(self, sql, params=None):
        self.sql = sql
        self.last_params = params or ()
        return self

    def fetchone(self):
        if not self.last_params:
            return None
        key = str(self.last_params[0])
        if key in self.rows:
            return self.rows[key]
        return None

    def fetchall(self):
        return []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _FakeConn:
    def __init__(self, rows: dict):
        self.cursor_obj = _FakeCursor(rows)

    def execute(self, sql, params=None):
        return self.cursor_obj.execute(sql, params)

    def cursor(self):
        return self.cursor_obj

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


@pytest.fixture(autouse=True)
def _mock_db(monkeypatch):
    """Patch both DB entry points (harness conn + loader connect)."""
    import backend.db.queries as q
    import backend.standards.loader as loader

    merged = {}
    for slug, row in _STD_ROWS.items():
        merged[slug] = row  # dict rows (harness `dict(row)` path)
    for slug, row in _LOADER_TUPLE.items():
        merged[slug] = row  # tuple rows (loader positional path)
    fake = _FakeConn(merged)
    monkeypatch.setattr(q.psycopg, "connect", lambda *a, **k: fake)
    monkeypatch.setattr(loader.psycopg, "connect", lambda *a, **k: fake)
    return fake


# ── select_variant — deterministic branches ───────────────────────────────

class TestSelectVariantDeterministic:
    def test_no_variants_returns_none(self):
        assert gf.select_variant("anything", "spec", _STD_ROWS["strong-oracle"]) is None

    def test_single_variant_returns_only_option(self):
        assert gf.select_variant("anything", "spec", _STD_ROWS["single-variant"]) == "mono"

    def test_multi_llm_pick_returns_chosen(self, monkeypatch):
        monkeypatch.setattr(
            gf, "call_llm_structured",
            lambda prompt, schema: _make_choice("editorial-serif"),
        )
        assert (
            gf.select_variant("write a long-form essay", "editorial intent", _STD_ROWS["design-layout-v2"])
            == "editorial-serif"
        )

    def test_llm_unknown_name_falls_back_first(self, monkeypatch):
        monkeypatch.setattr(
            gf, "call_llm_structured",
            lambda prompt, schema: _make_choice("not-a-real-variant"),
        )
        assert gf.select_variant("x", "y", _STD_ROWS["design-layout-v2"]) == "technical-dense"

    def test_llm_exception_falls_back_first(self, monkeypatch):
        def _boom(prompt, schema):
            raise RuntimeError("gateway down")

        monkeypatch.setattr(gf, "call_llm_structured", _boom)
        assert gf.select_variant("x", "y", _STD_ROWS["design-layout-v2"]) == "technical-dense"

    def test_prompt_never_asks_keyword_matching(self, monkeypatch):
        captured = {}

        def _capture(prompt, schema):
            captured["prompt"] = prompt
            return _make_choice("mono")

        monkeypatch.setattr(gf, "call_llm_structured", _capture)
        gf.select_variant("user prefers brutalism", "spec", _STD_ROWS["design-layout-v2"])
        assert "blurb" in captured["prompt"]
        assert "Raw user goal" in captured["prompt"]
        assert "NEVER invent" in captured["prompt"]


# ── _pin_variants + manifest reuse (guide 02.6) ───────────────────────────

class TestPinVariantsAndReuse:
    def _design_component(self):
        return gf.Component(standard_slug="design-layout-v2", subdir="design")

    def test_pins_variant_on_design_component(self, monkeypatch):
        monkeypatch.setattr(
            gf, "call_llm_structured", lambda prompt, schema: _make_choice("soft-clay"),
        )
        comp = self._design_component()
        gf._pin_variants([comp], "friendly marketing page", "warm tone")
        assert comp.variant == "soft-clay"

    def test_skips_standard_without_variants(self, monkeypatch):
        comp = gf.Component(standard_slug="strong-oracle", subdir=".")
        gf._pin_variants([comp], "x", "y")
        assert comp.variant is None

    def test_reuses_manifest_pin_when_present(self, tmp_path, monkeypatch):
        project = tmp_path / "proj"
        (project / ".conductor").mkdir(parents=True)
        (project / ".conductor" / "workspace.json").write_text(
            json.dumps({
                "layout": "subdirs",
                "components": [
                    {"standard_slug": "design-layout-v2", "subdir": "design", "variant": "brutalism"}
                ],
            }),
            encoding="utf-8",
        )
        monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setattr(
            gf, "call_llm_structured",
            lambda prompt, schema: (_ for _ in ()).throw(AssertionError("must not re-pick")),
        )
        comp = self._design_component()
        gf._pin_variants([comp], "something totally different", "spec", project_id="proj")
        assert comp.variant == "brutalism"

    def test_no_manifest_pin_triggers_pick(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setattr(
            gf, "call_llm_structured", lambda prompt, schema: _make_choice("mono"),
        )
        comp = self._design_component()
        gf._pin_variants([comp], "matrix dashboard", "spec", project_id="missing-proj")
        assert comp.variant == "mono"


# ── component_manifest_entry — variant pin (guide 02.6 shape) ──────────────

class TestManifestEntryVariant:
    def test_entry_carries_variant_when_given(self):
        entry = hw.component_manifest_entry(
            _STD_ROWS["design-layout-v2"], {}, "design", variant="editorial-serif",
        )
        assert entry["variant"] == "editorial-serif"
        assert entry["subdir"] == "design"
        assert entry["standard_slug"] == "design-layout-v2"

    def test_entry_omits_variant_when_absent(self):
        entry = hw.component_manifest_entry(_STD_ROWS["design-layout-v2"], {}, "design")
        assert "variant" not in entry


# ── generate_workspace — variant seeding (guide 02.7) ──────────────────────

def _make_scaffold(tmp_path) -> Path:
    src = tmp_path / "scaffold"
    (src / "variants" / "brutalism").mkdir(parents=True)
    (src / "variants" / "brutalism" / "DESIGN.md").write_text("# Brutalism brand\n")
    (src / "variants" / "brutalism" / "tokens.css").write_text(":root{--fg:#000}\n")
    (src / "variants" / "brutalism" / "reference.html").write_text("<h1>ref</h1>\n")
    (src / "variants" / "soft-clay").mkdir(parents=True)
    (src / "variants" / "soft-clay" / "DESIGN.md").write_text("# Soft Clay brand\n")
    (src / "variants" / "soft-clay" / "tokens.css").write_text(":root{--fg:#333}\n")
    (src / "variants" / "soft-clay" / "reference.html").write_text("<h1>clay</h1>\n")
    (src / "DESIGN.md").write_text("# generic scaffold stub\n")
    (src / "work").mkdir()
    (src / "work" / ".gitkeep").write_text("")
    (src / "AGENTS.md").write_text("# Design Layout Conventions (v2 — variant system)\n")
    return src


def _make_repo(tmp_path, name="proj") -> Path:
    proj = tmp_path / name
    proj.mkdir()
    subprocess.run(["git", "-C", str(proj), "init"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(proj), "config", "user.email", "t@t"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(proj), "config", "user.name", "t"], check=True, capture_output=True)
    (proj / "README.md").write_text(f"# {name}\n")
    subprocess.run(["git", "-C", str(proj), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(proj), "commit", "-m", "init"], check=True, capture_output=True)
    return proj


def _patch_scaffold_ref(monkeypatch, src, slug="design-layout-v2"):
    _std = dict(_STD_ROWS[slug])
    _std["scaffold_ref"] = str(src)
    monkeypatch.setattr(hw, "_get_active_standard", lambda s: _std)
    return _std


class TestGenerateWorkspaceSeeding:
    def test_seeds_variant_files_and_overrides_generic_stub(self, tmp_path, monkeypatch):
        src = _make_scaffold(tmp_path)
        proj = _make_repo(tmp_path, "seedy")
        _patch_scaffold_ref(monkeypatch, src)
        hw.generate_workspace(proj, "design-layout-v2", subdir="design", variant="brutalism", defer_commit=True)
        assert (proj / "design" / "DESIGN.md").read_text() == "# Brutalism brand\n"
        assert (proj / "design" / "work" / "tokens.css").read_text() == ":root{--fg:#000}\n"
        assert (proj / "design" / "work" / "reference.html").read_text() == "<h1>ref</h1>\n"

    def test_manifest_records_variant(self, tmp_path, monkeypatch):
        src = _make_scaffold(tmp_path)
        proj = _make_repo(tmp_path, "manifesty")
        _patch_scaffold_ref(monkeypatch, src)
        m = hw.generate_workspace(proj, "design-layout-v2", subdir="design", variant="soft-clay", defer_commit=True)
        assert m["variant"] == "soft-clay"
        disk = json.loads((proj / "design" / ".conductor" / "workspace.json").read_text())
        assert disk["variant"] == "soft-clay"

    def test_unknown_variant_rejected(self, tmp_path, monkeypatch):
        src = _make_scaffold(tmp_path)
        proj = _make_repo(tmp_path, "rej")
        _patch_scaffold_ref(monkeypatch, src)
        with pytest.raises(ValueError, match="Unknown variant 'bogus'"):
            hw.generate_workspace(proj, "design-layout-v2", subdir="design", variant="bogus", defer_commit=True)

    def test_no_variant_seeds_nothing(self, tmp_path, monkeypatch):
        src = _make_scaffold(tmp_path)
        proj = _make_repo(tmp_path, "plain")
        _patch_scaffold_ref(monkeypatch, src)
        hw.generate_workspace(proj, "design-layout-v2", subdir="design", defer_commit=True)
        assert (proj / "design" / "work" / "tokens.css").exists() is False
        assert (proj / "design" / "DESIGN.md").read_text() == "# generic scaffold stub\n"


# ── existing-subdir protection (guide 03.2 — regeneration never clobbers) ──

class TestExistingSubdirProtection:
    def test_component_subdirs_are_isolated(self, tmp_path, monkeypatch):
        src = _make_scaffold(tmp_path)
        proj = _make_repo(tmp_path, "iso")
        _patch_scaffold_ref(monkeypatch, src)
        hw.generate_workspace(proj, "design-layout-v2", subdir="design", variant="brutalism", defer_commit=True)
        (proj / "design" / "DESIGN.md").write_text("// project-adjusted\n")
        # A separate component subdir is unaffected (subdir isolation).
        hw.generate_workspace(proj, "design-layout-v2", subdir="other", variant="brutalism", defer_commit=True)
        assert (proj / "design" / "DESIGN.md").read_text() == "// project-adjusted\n"
        assert (proj / "other" / "DESIGN.md").read_text() == "# Brutalism brand\n"

# ── guide 03.3 — design-token handoff into a frontend component ─────────────


def _write_manifest(dir_: Path, components: list[dict]) -> None:
    dir_.mkdir(parents=True, exist_ok=True)
    (dir_ / ".conductor").mkdir(parents=True, exist_ok=True)
    (dir_ / ".conductor" / "workspace.json").write_text(
        json.dumps({"layout": "subdirs", "components": components}, indent=2),
        encoding="utf-8",
    )


def test_handoff_design_tokens_copies_tokens_and_records_sha(tmp_path: Path) -> None:
    design = tmp_path / "deps" / "design"
    (design / "work").mkdir(parents=True)
    (design / "scripts").mkdir(parents=True)
    (design / "work" / "tokens.css").write_text(
        "--fg: #1a1a1a;\n--bg: #ffffff;\n", encoding="utf-8"
    )
    (design / "scripts" / "check_tokens.py").write_text(
        "#!/usr/bin/env python3\nprint('ok')\n", encoding="utf-8"
    )
    materialized = [
        {"dep_name": "design", "dep_project_id": "proj-design", "path": str(design)}
    ]

    proj = tmp_path / "proj"
    frontend_subdir = "frontend"
    _write_manifest(
        proj,
        [{"subdir": frontend_subdir, "standard_slug": "react-frontend", "standard_id": "x"}],
    )
    wt = tmp_path / "wt"
    (wt / frontend_subdir).mkdir(parents=True)

    entry = hw.handoff_design_tokens(proj, wt, materialized, dep_shas={"proj-design": "abc123"})

    assert entry is not None
    assert entry["token_source"]["sha"] == "abc123"
    copied = wt / frontend_subdir / "src" / "styles" / "tokens.css"
    gate = wt / frontend_subdir / "scripts" / "check_tokens.py"
    assert copied.exists() and copied.read_text() == "--fg: #1a1a1a;\n--bg: #ffffff;\n"
    assert gate.exists()
    manifest = json.loads((proj / ".conductor" / "workspace.json").read_text())
    assert manifest["components"][0]["token_source"]["sha"] == "abc123"


def test_handoff_design_tokens_skips_no_frontend(tmp_path: Path) -> None:
    proj = tmp_path / "proj"
    _write_manifest(proj, [{"subdir": "api", "standard_slug": "python-backend"}])
    wt = tmp_path / "wt"
    wt.mkdir(parents=True)
    assert hw.handoff_design_tokens(proj, wt, []) is None


def test_handoff_design_tokens_skips_no_design_dep(tmp_path: Path) -> None:
    proj = tmp_path / "proj"
    _write_manifest(proj, [{"subdir": "frontend", "standard_slug": "react-frontend"}])
    wt = tmp_path / "wt"
    (wt / "frontend").mkdir(parents=True)
    assert hw.handoff_design_tokens(proj, wt, []) is None
