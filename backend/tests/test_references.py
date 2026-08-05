"""Unit tests for the per-project references store (backend/planning/references.py)."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.planning.references import (
    copy_references,
    gitignore_references,
    has_references,
    project_references_dir,
    references_in_worktree,
)


@pytest.fixture
def refs_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a fake references store root and point the module at it."""
    store = tmp_path / "references"
    store.mkdir()
    monkeypatch.setattr("backend.planning.references.REFERENCES_ROOT", store)
    return store


def _write_ref(store: Path, project_id: str, files: dict[str, str]) -> None:
    ref_dir = store / project_id
    for rel, content in files.items():
        p = ref_dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")


def test_project_references_dir_absolute(refs_store: Path) -> None:
    assert project_references_dir("alpha") == refs_store / "alpha"


def test_has_references_false_when_missing(refs_store: Path) -> None:
    assert has_references("nope") is False


def test_has_references_false_without_readme(refs_store: Path) -> None:
    _write_ref(refs_store, "alpha", {"notes.txt": "hi"})
    assert has_references("alpha") is False


def test_has_references_true_with_readme(refs_store: Path) -> None:
    _write_ref(refs_store, "alpha", {"README.md": "# Context"})
    assert has_references("alpha") is True


def test_copy_references_skips_missing(refs_store: Path, tmp_path: Path) -> None:
    wt = tmp_path / "wt"
    wt.mkdir()
    assert copy_references("missing", wt) is None
    assert not references_in_worktree(wt).exists()


def test_copy_references_skips_without_readme(refs_store: Path, tmp_path: Path) -> None:
    _write_ref(refs_store, "alpha", {"notes.txt": "hi"})
    wt = tmp_path / "wt"
    wt.mkdir()
    assert copy_references("alpha", wt) is None


def test_copy_references_copies_tree(refs_store: Path, tmp_path: Path) -> None:
    _write_ref(refs_store, "alpha", {"README.md": "# A", "docs/spec.md": "spec"})
    wt = tmp_path / "wt"
    wt.mkdir()
    dst = copy_references("alpha", wt)
    assert dst == references_in_worktree(wt)
    assert (wt / ".conductor/references/README.md").read_text() == "# A"
    assert (wt / ".conductor/references/docs/spec.md").read_text() == "spec"


def test_copy_references_excludes_git(refs_store: Path, tmp_path: Path) -> None:
    _write_ref(
        refs_store,
        "alpha",
        {"README.md": "# A", ".git/config": "not copied", "src/main.py": "x"},
    )
    wt = tmp_path / "wt"
    wt.mkdir()
    copy_references("alpha", wt)
    assert not (wt / ".conductor/references/.git").exists()
    assert (wt / ".conductor/references/src/main.py").exists()


def test_copy_references_overwrites_existing(refs_store: Path, tmp_path: Path) -> None:
    _write_ref(refs_store, "alpha", {"README.md": "v2"})
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".conductor/references/README.md").parent.mkdir(parents=True)
    (wt / ".conductor/references/README.md").write_text("v1", encoding="utf-8")
    copy_references("alpha", wt)
    assert (wt / ".conductor/references/README.md").read_text() == "v2"


def test_gitignore_references_adds_line(tmp_path: Path) -> None:
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".gitignore").write_text(".plan/\n.conductor/\n", encoding="utf-8")
    gitignore_references(wt)
    content = (wt / ".gitignore").read_text()
    assert ".conductor/references/" in content
    assert ".plan/" in content


def test_gitignore_references_creates_file(tmp_path: Path) -> None:
    wt = tmp_path / "wt"
    wt.mkdir()
    gitignore_references(wt)
    assert ".conductor/references/" in (wt / ".gitignore").read_text()


def test_gitignore_references_idempotent(tmp_path: Path) -> None:
    wt = tmp_path / "wt"
    wt.mkdir()
    gitignore_references(wt)
    gitignore_references(wt)
    assert (wt / ".gitignore").read_text().count(".conductor/references/") == 1
