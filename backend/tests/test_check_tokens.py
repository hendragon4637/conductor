"""Tests for conductor_variants File 03.1/03.2 — check_tokens deterministic gates.

Covers the non-token deterministic checks added in the design gate:
img_alt (every <img> has an alt attribute) and exports_valid (deliverables
usable: non-empty, HTML parses, PDF carries the %PDF- magic header).  The
token-conformance checks (contrast_aa / tokens_used / no_lorem / raw-literal
rejection) are also spot-verified against the curated variants to guarantee
the gate still passes on a clean component.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
import tempfile

import pytest

_SCAFFOLD = pathlib.Path(
    "/opt/aipc/conductor/scaffolds_store/design-layout-v2/scripts/check_tokens.py"
)

_SOURCE = importlib.util.spec_from_file_location("check_tokens", _SCAFFOLD)
assert _SOURCE is not None
_MOD = importlib.util.module_from_spec(_SOURCE)
assert _SOURCE.loader is not None
_SOURCE.loader.exec_module(_MOD)


def _run_capture(tmp: pathlib.Path) -> tuple[int, str]:
    import contextlib
    import io

    old = sys.argv[:]
    sys.argv = ["check_tokens", str(tmp)]
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            code = _MOD.main()
    finally:
        sys.argv = old
    return code, buf.getvalue()


@pytest.fixture()
def worktree(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    """A minimal valid component: tokens.css + one token-referencing file."""
    w = tmp_path / "work"
    w.mkdir()
    (tmp_path / "tokens.css").write_text(
        "--fg: #1a1a1a;\n--bg: #ffffff;\n--accent: #c62b1e;\n"
        "--accent-on: #ffffff;\n--space-1: 4px;\n--space-2: 8px;\n",
        encoding="utf-8",
    )
    return tmp_path, w


def test_all_curated_variants_still_pass() -> None:
    variants = _SCAFFOLD.parent.parent / "variants"
    checked = 0
    for vd in variants.iterdir():
        if not vd.is_dir():
            continue
        code, out = _run_capture(vd)
        assert code == 0, f"{vd.name} failed: {out}"
        checked += 1
    assert checked == 5, f"expected 5 variants, checked {checked}"


def test_img_alt_ok(worktree: tuple[pathlib.Path, pathlib.Path]) -> None:
    tmp, w = worktree
    (w / "index.html").write_text(
        '<p style="color:var(--fg)"><img src="a.png" alt="desc"></p>',
        encoding="utf-8",
    )
    code, out = _run_capture(tmp)
    assert code == 0, out


def test_img_missing_alt_fails(worktree: tuple[pathlib.Path, pathlib.Path]) -> None:
    tmp, w = worktree
    (w / "index.html").write_text(
        '<p style="color:var(--fg)"><img src="a.png"></p>',
        encoding="utf-8",
    )
    code, out = _run_capture(tmp)
    assert code == 1
    assert "alt attribute" in out


def test_exports_html_parses_and_pdf_opens(
    worktree: tuple[pathlib.Path, pathlib.Path],
) -> None:
    tmp, w = worktree
    ex = tmp / "exports"
    ex.mkdir()
    (w / "index.html").write_text(
        '<p style="color:var(--fg)">ok</p>', encoding="utf-8"
    )
    (ex / "index.html").write_text(
        '<section style="color:var(--fg)"><h1>t</h1></section>' + " " * 1200,
        encoding="utf-8",
    )
    (ex / "out.pdf").write_bytes(b"%PDF-1.7\n" + b"00000 0 n \n" * 120)
    code, out = _run_capture(tmp)
    assert code == 0, out


def test_exports_unclosed_html_fails(
    worktree: tuple[pathlib.Path, pathlib.Path],
) -> None:
    tmp, w = worktree
    ex = tmp / "exports"
    ex.mkdir()
    (w / "index.html").write_text(
        '<p style="color:var(--fg)">ok</p>', encoding="utf-8"
    )
    (ex / "index.html").write_text(
        '<div style="color:var(--fg)"><div>' + " " * 1200, encoding="utf-8"
    )
    code, out = _run_capture(tmp)
    assert code == 1
    assert "unclosed" in out


def test_exports_bad_pdf_fails(worktree: tuple[pathlib.Path, pathlib.Path]) -> None:
    tmp, w = worktree
    ex = tmp / "exports"
    ex.mkdir()
    (w / "index.html").write_text(
        '<p style="color:var(--fg)">ok</p>', encoding="utf-8"
    )
    (ex / "index.html").write_text(
        '<section style="color:var(--fg)"></section>' + " " * 1200,
        encoding="utf-8",
    )
    (ex / "bad.pdf").write_bytes(b"!notapdf!" + b"0 0 n \n" * 300)
    code, out = _run_capture(tmp)
    assert code == 1
    assert "%PDF-" in out


def test_raw_hex_still_rejected(worktree: tuple[pathlib.Path, pathlib.Path]) -> None:
    tmp, w = worktree
    (w / "index.html").write_text(
        '<p style="color:#ff0000">raw</p>', encoding="utf-8"
    )
    code, out = _run_capture(tmp)
    assert code == 1
    assert "raw color" in out


def test_no_tokens_reference_fails(worktree: tuple[pathlib.Path, pathlib.Path]) -> None:
    tmp, w = worktree
    (w / "index.html").write_text("<p>no tokens here</p>", encoding="utf-8")
    code, out = _run_capture(tmp)
    assert code == 1
    assert "tokens_used" in out


# ── frontend layout (guide 03.3 handoff) ────────────────────────────────────


@pytest.fixture()
def frontend_tree(tmp_path: pathlib.Path) -> pathlib.Path:
    """A minimal frontend component: tokens copied to src/styles/tokens.css."""
    fe = tmp_path / "frontend"
    styles = fe / "src" / "styles"
    styles.mkdir(parents=True)
    (styles / "tokens.css").write_text(
        "--fg: #1a1a1a;\n--bg: #ffffff;\n--accent: #c62b1e;\n",
        encoding="utf-8",
    )
    return fe


def test_frontend_clean_passes(frontend_tree: pathlib.Path) -> None:
    (frontend_tree / "src" / "App.tsx").write_text(
        'export const App = () => <p style={{ color: "var(--fg)" }}>ok</p>;',
        encoding="utf-8",
    )
    code, out = _run_capture(frontend_tree)
    assert code == 0, out


def test_frontend_raw_hex_fails(frontend_tree: pathlib.Path) -> None:
    (frontend_tree / "src" / "theme.ts").write_text(
        'export const c = { color: "#ff0000" };', encoding="utf-8"
    )
    code, out = _run_capture(frontend_tree)
    assert code == 1
    assert "raw color" in out


def test_frontend_no_tokens_fails(frontend_tree: pathlib.Path) -> None:
    (frontend_tree / "src" / "App.css").write_text(
        ".hero { padding: 8px; }\n", encoding="utf-8"
    )
    code, out = _run_capture(frontend_tree)
    assert code == 1
    assert "tokens_used" in out


def test_frontend_pure_logic_exempt_from_tokens_used(frontend_tree: pathlib.Path) -> None:
    (frontend_tree / "src" / "util.ts").write_text("export const a = 1;", encoding="utf-8")
    code, out = _run_capture(frontend_tree)
    assert code == 0, out


def test_frontend_tsx_scan_and_design_layout_unchanged(
    frontend_tree: pathlib.Path,
    worktree: tuple[pathlib.Path, pathlib.Path],
    tmp_path: pathlib.Path,
) -> None:
    # TSX scanned in frontend layout
    (frontend_tree / "src" / "App.tsx").write_text(
        'export const App = () => <div style={{ color: "var(--fg)" }}><img src="x" /></div>;',
        encoding="utf-8",
    )
    # A .tsx file with a raw hex triggers the literal check
    (frontend_tree / "src" / "App.tsx").write_text(
        'export const App = () => <div style={{ background: "#0f0f0f" }} />;',
        encoding="utf-8",
    )
    code, out = _run_capture(frontend_tree)
    assert code == 1
    assert "raw color" in out
    # Design layout unaffected: a tsx file is not part of the design scan
    design = tmp_path / "design"
    (design / "work").mkdir(parents=True)
    (design / "tokens.css").write_text(
        "--fg: #1a1a1a;\n--bg: #ffffff;\n--accent: #c62b1e;\n--accent-on: #ffffff;\n"
        "--space-1: 4px;\n--space-2: 8px;\n",
        encoding="utf-8",
    )
    (design / "work" / "index.html").write_text(
        '<p style="color:var(--fg)">ok</p>', encoding="utf-8"
    )
    code2, out2 = _run_capture(design)
    assert code2 == 0, out2