"""Tests for File 02 formulation menu (T-02.3): delivery_form in selector + prompt."""

from __future__ import annotations

import pytest

from backend.standards.loader import list_standard_menu


def _fake_rows():
    return [
        ("python-backend", "Python Backend (FastAPI)", "HTTP API server", ["software"], "backend", "served_url"),
        ("cli-tool", "Python CLI Tool", "Command-line tool", ["software"], ".", "installed_command"),
        ("design-layout", "Design Layout", "Design deliverables", ["design", "creative"], "design", ""),
    ]


@pytest.fixture(autouse=True)
def _mock_db(monkeypatch):
    """Patch psycopg.connect to return canned domain_standards rows."""
    class FakeCursor:
        def __init__(self):
            self.sql = ""
            self.rows = _fake_rows()

        def execute(self, sql, params=None):
            self.sql = sql

        def fetchall(self):
            return self.rows

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class FakeConn:
        def __init__(self):
            self.cursor_obj = FakeCursor()

        def cursor(self):
            return self.cursor_obj

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    fake = FakeConn()

    def _connect(url):
        return fake

    monkeypatch.setattr("backend.standards.loader.psycopg.connect", _connect)
    monkeypatch.setattr("backend.standards.loader.get_db_url", lambda: "postgresql://fake")
    return fake


class TestListStandardMenuDeliveryForm:
    def test_menu_includes_delivery_form_key(self):
        menu = list_standard_menu()
        assert all("delivery_form" in m for m in menu)

    def test_delivery_form_values_present(self):
        by_slug = {m["slug"]: m["delivery_form"] for m in list_standard_menu()}
        assert by_slug["python-backend"] == "served_url"
        assert by_slug["cli-tool"] == "installed_command"

    def test_missing_delivery_form_defaults_to_empty_string(self):
        by_slug = {m["slug"]: m["delivery_form"] for m in list_standard_menu()}
        assert by_slug["design-layout"] == ""

    def test_selector_derives_delivery_form_from_artifact_spec(self, _mock_db):
        list_standard_menu()
        sql = _mock_db.cursor_obj.sql
        assert "artifact_spec->'delivery_spec'->>'form' AS delivery_form" in sql
        assert "delivery_form" in sql


class TestFormulatePromptRendering:
    """The formulator's standards table must render delivery_form (guide 02.3)."""

    MENU = [
        {
            "slug": "python-backend",
            "name": "Python Backend (FastAPI)",
            "blurb": "HTTP API server",
            "families": ["software"],
            "default_subdir": "backend",
            "delivery_form": "served_url",
        },
        {
            "slug": "design-layout",
            "name": "Design Layout",
            "blurb": "Design deliverables",
            "families": ["design", "creative"],
            "default_subdir": "design",
            "delivery_form": "",
        },
    ]

    def _capture(self, monkeypatch):
        captured = {}

        def _fake_llm(prompt, schema):
            captured["prompt"] = prompt
            return schema(
                goal="Build a web app",
                spec="spec",
                quality_intent="quality",
                needs_clarification=False,
                questions=[],
                standard_ids=["python-backend"],
                estimated_node_count=3,
            )

        monkeypatch.setattr(
            "backend.planning.meta_planner.goal_formulator.call_llm_structured", _fake_llm
        )
        return captured

    def test_prompt_shows_delivery_form(self, monkeypatch):
        from backend.planning.meta_planner.goal_formulator import formulate

        captured = self._capture(monkeypatch)
        formulate("build an api", valid_standards=self.MENU)
        assert "python-backend | served_url" in captured["prompt"]

    def test_prompt_renders_missing_delivery_form_as_n_a(self, monkeypatch):
        from backend.planning.meta_planner.goal_formulator import formulate

        captured = self._capture(monkeypatch)
        formulate("design a layout", valid_standards=self.MENU)
        assert "design-layout | n/a" in captured["prompt"]

    def test_prompt_keeps_blurb_and_families(self, monkeypatch):
        from backend.planning.meta_planner.goal_formulator import formulate

        captured = self._capture(monkeypatch)
        formulate("build an api", valid_standards=self.MENU)
        assert "HTTP API server" in captured["prompt"]
        assert "[software]" in captured["prompt"]
