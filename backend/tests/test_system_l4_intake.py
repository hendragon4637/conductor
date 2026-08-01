"""Tests for L4 intake flow: SystemPlan validation, proposals CRUD,
ratify_system, queue_first_goals, extract_system_plan, and standards_menu."""
from __future__ import annotations

import json
from unittest import mock

import pytest

from services.planner.system_goal import (
    ProjectDef,
    SystemPlan,
    extract_system_plan,
    list_proposals,
    queue_first_goals,
    ratify_system,
    save_proposal,
    standards_menu,
    update_proposal_status,
    validate_system_plan,
)


# ── Pydantic models ─────────────────────────────────────────────────────────

class TestProjectDef:
    def test_minimal(self):
        p = ProjectDef(name="api")
        assert p.name == "api"
        assert p.kind == "component"
        assert p.domain == "general"

    def test_full(self):
        p = ProjectDef(
            name="web",
            kind="assembly",
            domain="frontend",
            description="The web UI",
            depends_on=["api"],
            first_goal="Build the React frontend shell",
        )
        assert p.first_goal == "Build the React frontend shell"
        assert p.depends_on == ["api"]


class TestSystemPlan:
    def test_valid_plan(self):
        plan = SystemPlan(
            system_name="ecommerce",
            projects=[
                ProjectDef(name="db", first_goal="Set up PostgreSQL schema"),
                ProjectDef(name="api", depends_on=["db"], first_goal="Build REST API"),
            ],
        )
        assert plan.system_name == "ecommerce"
        assert len(plan.projects) == 2

    def test_rejects_missing_dep(self):
        with pytest.raises(ValueError, match="depends on 'missing' which is not defined"):
            SystemPlan(
                system_name="broken",
                projects=[
                    ProjectDef(name="api", depends_on=["missing"]),
                ],
            )

    def test_min_projects(self):
        with pytest.raises(ValueError, match="List should have at least 1 item"):
            SystemPlan(system_name="empty", projects=[])


# ── validate_system_plan() ──────────────────────────────────────────────────

class TestValidateSystemPlan:
    def _make(self, projects: list[ProjectDef]) -> SystemPlan:
        return SystemPlan(system_name="test", projects=projects)

    def test_all_valid(self):
        plan = self._make([
            ProjectDef(name="db", first_goal="Set up PostgreSQL schema"),
            ProjectDef(name="api", depends_on=["db"], first_goal="Build the REST API"),
            ProjectDef(name="web", depends_on=["api"], first_goal="Build the React frontend with login and dashboard"),
        ])
        assert validate_system_plan(plan) == []

    def test_duplicate_names(self):
        plan = self._make([
            ProjectDef(name="dup", first_goal="First one with enough chars"),
            ProjectDef(name="dup", first_goal="Second one also enough chars here"),
            ProjectDef(name="other", first_goal="Third project here enough chars"),
        ])
        errors = validate_system_plan(plan)
        assert any("Duplicate" in e and "dup" in e for e in errors)

    def test_cycle_detected(self):
        plan = self._make([
            ProjectDef(name="a", depends_on=["b"], first_goal="Project a with enough chars"),
            ProjectDef(name="b", depends_on=["c"], first_goal="Project b with enough chars"),
            ProjectDef(name="c", depends_on=["a"], first_goal="Project c with enough chars"),
        ])
        errors = validate_system_plan(plan)
        assert any("Cycle" in e for e in errors)

    def test_missing_first_goal(self):
        plan = self._make([
            ProjectDef(name="a", first_goal=""),
            ProjectDef(name="b", first_goal="Enough chars here for project b"),
            ProjectDef(name="c", first_goal="Enough chars here for project c"),
        ])
        errors = validate_system_plan(plan)
        assert any("no first_goal" in e for e in errors)

    def test_short_first_goal(self):
        plan = self._make([
            ProjectDef(name="a", first_goal="Short"),
            ProjectDef(name="b", first_goal="Enough chars here for project b"),
            ProjectDef(name="c", first_goal="Enough chars here for project c"),
        ])
        errors = validate_system_plan(plan)
        assert any("too short" in e for e in errors)

    def test_too_few_projects(self):
        plan = self._make([
            ProjectDef(name="a", first_goal="Enough chars here for project a"),
            ProjectDef(name="b", first_goal="Enough chars here for project b"),
        ])
        errors = validate_system_plan(plan)
        assert any("only 2" in e for e in errors)

    def test_too_many_projects(self):
        plan = self._make([
            ProjectDef(name=f"p{i}", first_goal=f"Enough chars for project {i}")
            for i in range(8)
        ])
        errors = validate_system_plan(plan)
        assert any("8 projects" in e for e in errors)

    def test_dep_refs_nonexistent(self):
        """SystemPlan model validator catches refs to undefined projects."""
        with pytest.raises(ValueError, match="depends on 'ghost'"):
            SystemPlan(
                system_name="test",
                projects=[
                    ProjectDef(name="a", depends_on=["ghost"], first_goal="Enough chars here for a"),
                    ProjectDef(name="b", first_goal="Enough chars here for b"),
                    ProjectDef(name="c", first_goal="Enough chars here for c"),
                ],
            )

    def test_multiple_errors(self):
        plan = self._make([
            ProjectDef(name="dup", first_goal=""),
            ProjectDef(name="dup", first_goal=""),
            ProjectDef(name="c", first_goal=""),
        ])
        errors = validate_system_plan(plan)
        assert len(errors) >= 2  # duplicate + missing first_goals + too few


# ── standards_menu() ────────────────────────────────────────────────────────

class TestStandardsMenu:
    @mock.patch("services.planner.system_goal.os.environ", {"DATABASE_URL": ""})
    def test_no_db_url_returns_empty(self):
        assert standards_menu() == []

    @mock.patch("psycopg.connect")
    @mock.patch("services.planner.system_goal.os.environ", {"DATABASE_URL": "postgres://localhost/db"})
    def test_returns_standards(self, mock_connect):
        mock_cur = mock.MagicMock()
        mock_cur.fetchall.return_value = [
            {"slug": "fastapi-crud", "name": "FastAPI CRUD", "kind": "backend",
             "families": ["software"], "service_template": '{"port": 8000}'},
        ]
        mock_conn = mock_connect.return_value.__enter__.return_value
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        result = standards_menu()
        assert len(result) == 1
        assert result[0]["slug"] == "fastapi-crud"
        assert result[0]["service_template"] == {"port": 8000}

    @mock.patch("psycopg.connect")
    @mock.patch("services.planner.system_goal.os.environ", {"DATABASE_URL": "postgres://localhost/db"})
    def test_filters_by_families(self, mock_connect):
        mock_cur = mock.MagicMock()
        mock_cur.fetchall.return_value = [
            {"slug": "react-ui", "name": "React UI", "kind": "frontend",
             "families": ["software"], "service_template": None},
        ]
        mock_conn = mock_connect.return_value.__enter__.return_value
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        result = standards_menu(families=["software"])
        assert len(result) == 1

    @mock.patch("psycopg.connect")
    @mock.patch("services.planner.system_goal.os.environ", {"DATABASE_URL": "postgres://localhost/db"})
    def test_db_error_returns_empty(self, mock_connect):
        mock_connect.side_effect = RuntimeError("DB down")
        assert standards_menu() == []

    @mock.patch("psycopg.connect")
    @mock.patch("services.planner.system_goal.os.environ", {"DATABASE_URL": "postgres://localhost/db"})
    def test_service_template_json_parse_failure(self, mock_connect):
        mock_cur = mock.MagicMock()
        mock_cur.fetchall.return_value = [
            {"slug": "bad-json", "name": "Bad JSON", "kind": "backend",
             "families": ["software"], "service_template": "{invalid json}"},
        ]
        mock_conn = mock_connect.return_value.__enter__.return_value
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        result = standards_menu()
        assert result[0]["service_template"] == "{invalid json}"  # returned as-is


# ── Proposal CRUD ───────────────────────────────────────────────────────────

class TestSaveProposal:
    @mock.patch("psycopg.connect")
    @mock.patch("services.planner.system_goal.os.environ", {"DATABASE_URL": "postgres://localhost/db"})
    def test_saves_and_returns_id(self, mock_connect):
        mock_cur = mock.MagicMock()
        mock_cur.fetchone.return_value = (42,)
        mock_conn = mock_connect.return_value.__enter__.return_value
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        pid = save_proposal("build an app", {"system_name": "app"})
        assert pid == 42

    @mock.patch("psycopg.connect")
    @mock.patch("services.planner.system_goal.os.environ", {"DATABASE_URL": "postgres://localhost/db"})
    def test_with_edits(self, mock_connect):
        mock_cur = mock.MagicMock()
        mock_cur.fetchone.return_value = (7,)
        mock_conn = mock_connect.return_value.__enter__.return_value
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        pid = save_proposal("build", {"system_name": "app"}, edited={"system_name": "app-v2"})
        assert pid == 7

    @mock.patch("psycopg.connect")
    @mock.patch("services.planner.system_goal.os.environ", {"DATABASE_URL": "postgres://localhost/db"})
    def test_no_returned_id(self, mock_connect):
        mock_cur = mock.MagicMock()
        mock_cur.fetchone.return_value = None
        mock_conn = mock_connect.return_value.__enter__.return_value
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        pid = save_proposal("build", {"system_name": "app"})
        assert pid == 0


class TestGetProposal:
    @mock.patch("psycopg.connect")
    @mock.patch("services.planner.system_goal.os.environ", {"DATABASE_URL": "postgres://localhost/db"})
    def test_fetches_by_id(self, mock_connect):
        mock_cur = mock.MagicMock()
        mock_cur.fetchone.return_value = {"id": 1, "raw_input": "build", "status": "proposed"}
        mock_conn = mock_connect.return_value.__enter__.return_value
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        from services.planner.system_goal import get_proposal
        result = get_proposal(1)
        assert result["id"] == 1

    @mock.patch("psycopg.connect")
    @mock.patch("services.planner.system_goal.os.environ", {"DATABASE_URL": "postgres://localhost/db"})
    def test_not_found(self, mock_connect):
        mock_cur = mock.MagicMock()
        mock_cur.fetchone.return_value = None
        mock_conn = mock_connect.return_value.__enter__.return_value
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        from services.planner.system_goal import get_proposal
        assert get_proposal(999) is None


class TestListProposals:
    @mock.patch("psycopg.connect")
    @mock.patch("services.planner.system_goal.os.environ", {"DATABASE_URL": "postgres://localhost/db"})
    def test_list_all(self, mock_connect):
        mock_cur = mock.MagicMock()
        mock_cur.fetchall.return_value = [
            {"id": 2, "status": "proposed"},
            {"id": 1, "status": "ratified"},
        ]
        mock_conn = mock_connect.return_value.__enter__.return_value
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        result = list_proposals()
        assert len(result) == 2

    @mock.patch("psycopg.connect")
    @mock.patch("services.planner.system_goal.os.environ", {"DATABASE_URL": "postgres://localhost/db"})
    def test_filter_by_status(self, mock_connect):
        mock_cur = mock.MagicMock()
        mock_cur.fetchall.return_value = [
            {"id": 3, "status": "proposed"},
        ]
        mock_conn = mock_connect.return_value.__enter__.return_value
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        result = list_proposals(status="proposed")
        assert len(result) == 1

    @mock.patch("psycopg.connect")
    @mock.patch("services.planner.system_goal.os.environ", {"DATABASE_URL": "postgres://localhost/db"})
    def test_no_proposals(self, mock_connect):
        mock_cur = mock.MagicMock()
        mock_cur.fetchall.return_value = []
        mock_conn = mock_connect.return_value.__enter__.return_value
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        assert list_proposals() == []


class TestUpdateProposalStatus:
    @mock.patch("psycopg.connect")
    @mock.patch("services.planner.system_goal.os.environ", {"DATABASE_URL": "postgres://localhost/db"})
    def test_update_with_system_id(self, mock_connect):
        mock_cur = mock.MagicMock()
        mock_conn = mock_connect.return_value.__enter__.return_value
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        update_proposal_status(1, "ratified", system_id="sys-abc")
        sql = mock_cur.execute.call_args[0][0]
        assert "system_id" in sql

    @mock.patch("psycopg.connect")
    @mock.patch("services.planner.system_goal.os.environ", {"DATABASE_URL": "postgres://localhost/db"})
    def test_update_without_system_id(self, mock_connect):
        mock_cur = mock.MagicMock()
        mock_conn = mock_connect.return_value.__enter__.return_value
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        update_proposal_status(1, "rejected")
        sql = mock_cur.execute.call_args[0][0]
        assert "system_id" not in sql


# ── ratify_system() ─────────────────────────────────────────────────────────

class TestRatifySystem:
    @mock.patch("services.planner.system_goal.update_proposal_status")
    @mock.patch("services.planner.system_goal.os.environ", {"DATABASE_URL": "postgres://localhost/db"})
    def test_ratifies_and_returns_system_id(self, mock_update_status):
        fake_proposal = {
            "id": 1, "status": "proposed",
            "proposal": json.dumps({
                "system_name": "mysystem",
                "system_description": "Test system",
                "glossary": {"API": "Application Programming Interface"},
                "projects": [
                    {"name": "db", "kind": "component", "first_goal": "Set up the database schema with PostgreSQL",
                     "depends_on": []},
                    {"name": "api", "kind": "component", "first_goal": "Build REST API with CRUD endpoints",
                     "depends_on": ["db"]},
                ],
            }),
        }

        mock_cur = mock.MagicMock()
        # get_proposal returns the proposal
        # SELECT system_id FROM systems — not found (None)
        # INSERT INTO systems RETURNING system_id
        # SELECT project_id FROM projects — not found (None) × 2
        # INSERT INTO projects RETURNING project_id × 2
        mock_cur.fetchone.side_effect = [
            None,                          # systems check — not found
            {"system_id": "mysystem"},     # systems insert
            None,                          # projects check — not found
            {"project_id": "mysystem-db"},
            None,                          # projects check — not found
            {"project_id": "mysystem-api"},
        ]
        mock_cur.fetchall.return_value = []  # no deps for project_dependencies query

        mock_conn = mock.MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        with mock.patch("services.planner.system_goal.get_proposal", return_value=fake_proposal):
            with mock.patch("psycopg.connect", return_value=mock_conn):
                system_id = ratify_system(1)

        assert system_id == "mysystem"
        mock_update_status.assert_called_once_with(1, "ratified", "mysystem")

    @mock.patch("services.planner.system_goal.os.environ", {"DATABASE_URL": "postgres://localhost/db"})
    def test_raises_on_missing_proposal(self):
        with mock.patch("services.planner.system_goal.get_proposal", return_value=None):
            with pytest.raises(ValueError, match="Proposal 999 not found"):
                ratify_system(999)

    @mock.patch("services.planner.system_goal.os.environ", {"DATABASE_URL": "postgres://localhost/db"})
    def test_raises_on_wrong_status(self):
        fake = {"id": 1, "status": "ratified", "proposal": "{}"}
        with mock.patch("services.planner.system_goal.get_proposal", return_value=fake):
            with pytest.raises(ValueError, match="not 'proposed'"):
                ratify_system(1)

    @mock.patch("services.planner.system_goal.update_proposal_status")
    @mock.patch("services.planner.system_goal.os.environ", {"DATABASE_URL": "postgres://localhost/db"})
    def test_with_edited_overlay(self, mock_update_status):
        fake_proposal = {
            "id": 1, "status": "proposed",
            "proposal": json.dumps({
                "system_name": "original",
                "projects": [
                    {"name": "svc", "first_goal": "Original goal with enough chars to pass",
                     "depends_on": []},
                    {"name": "ui", "first_goal": "UI goal with enough chars to pass",
                     "depends_on": ["svc"]},
                ],
            }),
        }
        edited = {
            "system_name": "edited-name",
            "projects": [
                {"name": "svc", "first_goal": "Edited goal with enough chars to pass"},
                {"name": "ui", "first_goal": "Edited UI goal with enough chars to pass",
                 "depends_on": ["svc"]},
            ],
        }

        mock_cur = mock.MagicMock()
        mock_cur.fetchone.side_effect = [
            None,
            {"system_id": "edited-name"},
            None, {"project_id": "edited-name-svc"},
            None, {"project_id": "edited-name-ui"},
        ]
        mock_conn = mock.MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        with mock.patch("services.planner.system_goal.get_proposal", return_value=fake_proposal):
            with mock.patch("psycopg.connect", return_value=mock_conn):
                system_id = ratify_system(1, edited=edited)

        assert system_id == "edited-name"


# ── queue_first_goals() ─────────────────────────────────────────────────────

class TestQueueFirstGoals:
    @mock.patch("psycopg.connect")
    @mock.patch("services.planner.system_goal.os.environ", {"DATABASE_URL": "postgres://localhost/db"})
    def test_queues_all_projects(self, mock_connect):
        mock_cur = mock.MagicMock()
        mock_conn = mock_connect.return_value.__enter__.return_value
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        count = queue_first_goals(["p1", "p2", "p3"], "initial goal", origin="system_goal")
        assert count == 3
        assert mock_cur.execute.call_count == 3

    @mock.patch("psycopg.connect")
    @mock.patch("services.planner.system_goal.os.environ", {"DATABASE_URL": "postgres://localhost/db"})
    def test_empty_list(self, mock_connect):
        mock_cur = mock.MagicMock()
        mock_conn = mock_connect.return_value.__enter__.return_value
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        assert queue_first_goals([], "nothing") == 0


# ── extract_system_plan() ───────────────────────────────────────────────────

class TestExtractSystemPlan:
    @mock.patch("services.planner.system_goal.standards_menu", return_value=[])
    @mock.patch("backend.planning.meta_planner.llm.call_llm_structured")
    def test_first_attempt_succeeds(self, mock_llm, _mock_menu):
        plan = SystemPlan(
            system_name="test",
            projects=[
                ProjectDef(name="a", first_goal="First project with enough chars"),
                ProjectDef(name="b", depends_on=["a"], first_goal="Second project with enough chars here"),
                ProjectDef(name="c", depends_on=["b"], first_goal="Third project with enough chars here"),
            ],
        )
        mock_llm.return_value = plan

        result = extract_system_plan("test goal")
        assert result.system_name == "test"
        mock_llm.assert_called_once()

    @mock.patch("services.planner.system_goal.standards_menu", return_value=[])
    @mock.patch("backend.planning.meta_planner.llm.call_llm_structured")
    def test_retries_on_validation_failure(self, mock_llm, _mock_menu):
        invalid_plan = SystemPlan(
            system_name="test",
            projects=[
                ProjectDef(name="a", first_goal="short"),
                ProjectDef(name="b", first_goal="also short"),
                ProjectDef(name="c", first_goal="nope"),
            ],
        )
        valid_plan = SystemPlan(
            system_name="test",
            projects=[
                ProjectDef(name="a", first_goal="Now this is a long enough first goal"),
                ProjectDef(name="b", first_goal="This one also is long enough to pass validation"),
                ProjectDef(name="c", first_goal="Third project with enough chars here"),
            ],
        )
        mock_llm.side_effect = [invalid_plan, valid_plan]

        result = extract_system_plan("test goal")
        assert result.system_name == "test"
        assert mock_llm.call_count == 2

    @mock.patch("services.planner.system_goal.standards_menu", return_value=[])
    @mock.patch("backend.planning.meta_planner.llm.call_llm_structured")
    def test_exhausts_retries_and_returns_best_effort(self, mock_llm, _mock_menu):
        bad = SystemPlan(
            system_name="test",
            projects=[
                ProjectDef(name="a", first_goal="short"),
                ProjectDef(name="b", first_goal="also short"),
                ProjectDef(name="c", first_goal="nope"),
            ],
        )
        mock_llm.side_effect = [bad, bad, bad]

        result = extract_system_plan("test goal")
        assert result.system_name == "test"
        assert mock_llm.call_count == 3

    @mock.patch("services.planner.system_goal.standards_menu", return_value=[])
    @mock.patch("backend.planning.meta_planner.llm.call_llm_structured")
    def test_raises_when_all_attempts_fail(self, mock_llm, _mock_menu):
        """Returns last best-effort plan after exhausting retries."""
        bad_plan = SystemPlan(
            system_name="test",
            projects=[
                ProjectDef(name="a", first_goal="short"),
                ProjectDef(name="b", first_goal="also short"),
                ProjectDef(name="c", first_goal="nope"),
            ],
        )
        mock_llm.side_effect = [bad_plan, bad_plan, bad_plan]

        # Returns last best-effort plan (doesn't crash)
        result = extract_system_plan("test goal")
        assert result.system_name == "test"
        assert mock_llm.call_count == 3
