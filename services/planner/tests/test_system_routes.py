"""Tests for system-related route endpoints in planner main.py.

Covers:
  POST /system/goal
  POST /system/ratify/{proposal_id}
  POST /system/proposals/{proposal_id}/reject
  GET  /system/proposals
  GET  /system/{system_id}/queue
  GET  /system/{system_id}
  POST /system/{system_id}/assemble
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


# ── POST /system/goal ───────────────────────────────────────────────────────

class TestSystemGoal:
    @patch("services.planner.system_goal.extract_system_plan")
    @patch("services.planner.system_goal.save_proposal")
    def test_creates_proposal(self, mock_save, mock_extract, client):
        from services.planner.system_goal import ProjectDef, SystemPlan

        plan = SystemPlan(
            system_name="mysystem",
            projects=[
                ProjectDef(name="svc", first_goal="Build the main service with enough chars"),
                ProjectDef(name="ui", depends_on=["svc"],
                           first_goal="Build the React frontend with enough chars"),
                ProjectDef(name="db", depends_on=["svc"],
                           first_goal="Set up the database schema with enough chars"),
            ],
        )
        mock_extract.return_value = plan
        mock_save.return_value = 42

        resp = client.post("/system/goal", json={
            "raw_input": "build my system",
        })

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "proposed"
        assert data["proposal_id"] == 42
        assert data["proposal"]["system_name"] == "mysystem"

    @patch("services.planner.system_goal.extract_system_plan")
    def test_passes_families(self, mock_extract, client):
        from services.planner.system_goal import ProjectDef, SystemPlan

        mock_extract.return_value = SystemPlan(
            system_name="t",
            projects=[
                ProjectDef(name="a", first_goal="Project a long enough first goal here"),
                ProjectDef(name="b", first_goal="Project b long enough first goal here"),
                ProjectDef(name="c", first_goal="Project c long enough first goal here"),
            ],
        )

        client.post("/system/goal", json={
            "raw_input": "build",
            "families": ["software"],
        })

        _call_kwargs = mock_extract.call_args
        assert mock_extract.call_args[1].get("families") == ["software"]

    @patch("services.planner.system_goal.extract_system_plan")
    def test_handles_llm_failure(self, mock_extract, client_raw):
        mock_extract.side_effect = RuntimeError("LLM timeout")

        resp = client_raw.post("/system/goal", json={
            "raw_input": "build",
        })

        assert resp.status_code == 500
        assert "LLM timeout" in resp.json()["error"]


# ── POST /system/ratify/{proposal_id} ───────────────────────────────────────

class TestRatifySystemGoal:
    @patch("services.planner.system_goal.ratify_system")
    def test_ratifies_proposal(self, mock_ratify, client):
        mock_ratify.return_value = "sys-abc"

        resp = client.post("/system/ratify/1", json={})

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ratified"
        assert data["system_id"] == "sys-abc"
        mock_ratify.assert_called_once_with(1, edited=None, persona_id="default")

    @patch("services.planner.system_goal.ratify_system")
    def test_with_edited_and_persona(self, mock_ratify, client):
        mock_ratify.return_value = "sys-xyz"

        resp = client.post("/system/ratify/2", json={
            "edited": {"system_name": "renamed"},
            "persona_id": "admin",
        })

        assert resp.status_code == 200
        mock_ratify.assert_called_once_with(
            2, edited={"system_name": "renamed"}, persona_id="admin",
        )

    @patch("services.planner.system_goal.ratify_system")
    def test_value_error_returns_400(self, mock_ratify, client_raw):
        mock_ratify.side_effect = ValueError("Proposal not found")

        resp = client_raw.post("/system/ratify/999", json={})
        assert resp.status_code == 400
        assert "Proposal not found" in resp.json()["error"]

    @patch("services.planner.system_goal.ratify_system")
    def test_unexpected_error_returns_500(self, mock_ratify, client_raw):
        mock_ratify.side_effect = RuntimeError("DB crash")

        resp = client_raw.post("/system/ratify/1", json={})
        assert resp.status_code == 500
        assert "DB crash" in resp.json()["error"]


# ── POST /system/proposals/{proposal_id}/reject ─────────────────────────────

class TestRejectSystemProposal:
    @patch("services.planner.system_goal.update_proposal_status")
    def test_rejects_proposal(self, mock_update, client):
        resp = client.post("/system/proposals/1/reject")

        assert resp.status_code == 200
        assert resp.json()["status"] == "rejected"
        mock_update.assert_called_once_with(1, "rejected")

    @patch("services.planner.system_goal.update_proposal_status")
    def test_handles_error(self, mock_update, client_raw):
        mock_update.side_effect = RuntimeError("DB error")

        resp = client_raw.post("/system/proposals/1/reject")
        assert resp.status_code == 500


# ── GET /system/proposals ───────────────────────────────────────────────────

class TestListSystemProposals:
    @patch("services.planner.system_goal.list_proposals")
    def test_lists_proposals(self, mock_list, client):
        mock_list.return_value = [{"id": 1, "status": "proposed"}]

        resp = client.get("/system/proposals")

        assert resp.status_code == 200
        assert resp.json()["proposals"] == [{"id": 1, "status": "proposed"}]

    @patch("services.planner.system_goal.list_proposals")
    def test_filters_by_status(self, mock_list, client):
        mock_list.return_value = [{"id": 2, "status": "ratified"}]

        resp = client.get("/system/proposals?status=ratified")

        assert resp.status_code == 200
        mock_list.assert_called_once_with(status="ratified")


# ── GET /system/{system_id}/queue ──────────────────────────────────────────

class TestSystemQueue:
    @patch("services.planner.system_goal.get_system_queue")
    def test_returns_queue(self, mock_queue, client):
        mock_queue.return_value = [{"id": 1, "status": "pending"}]

        resp = client.get("/system/sys-abc/queue")

        assert resp.status_code == 200
        assert resp.json()["queue"] == [{"id": 1, "status": "pending"}]

    @patch("services.planner.system_goal.get_system_queue")
    def test_filters_by_status(self, mock_queue, client):
        client.get("/system/sys-abc/queue?status=pending")

        mock_queue.assert_called_once_with("sys-abc", status="pending")

    @patch("services.planner.system_goal.get_system_queue")
    def test_empty_queue(self, mock_queue, client):
        mock_queue.return_value = []

        resp = client.get("/system/sys-abc/queue")
        assert resp.json()["queue"] == []


# ── GET /system/{system_id} ────────────────────────────────────────────────

class TestGetSystem:
    @patch("psycopg.connect")
    def test_returns_system_details(self, mock_connect, client):
        mock_cur = MagicMock()
        mock_cur.fetchone.side_effect = [
            {"system_id": "sys-abc", "name": "My System", "description": "A system"},
            None,  # assembly — no assembly project
        ]
        mock_cur.fetchall.side_effect = [
            [  # projects
                {"project_id": "p1", "name": "API", "kind": "component",
                 "status": "active", "description": ""},
                {"project_id": "p2", "name": "UI", "kind": "component",
                 "status": "active", "description": ""},
            ],
            [  # edges
                {"project_id": "p2", "depends_on_project_id": "p1",
                 "dep_name": "API", "from_name": "UI", "to_name": "API"},
            ],
        ]

        # Each project call needs: last_run fetchone, next_goal fetchone
        # p1: last_run not found, next_goal found
        # p2: last_run found, next_goal not found
        mock_cur.fetchone.side_effect = [
            {"system_id": "sys-abc", "name": "My System", "description": "A system"},
            None,  # p1 last_run
            {"id": 101, "raw_input": "goal", "origin": "system_goal",
             "status": "pending", "plan_id": None, "created_at": "2026-01-01"},  # p1 next_goal
            {"id": "run1", "state": "done", "worktree_status": "merged",
             "l4_standalone": False, "created_at": "2026-01-01"},  # p2 last_run
            None,  # p2 next_goal
            None,  # assembly — not found
        ]

        mock_conn = mock_connect.return_value.__enter__.return_value
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        resp = client.get("/system/sys-abc")

        assert resp.status_code == 200
        data = resp.json()
        assert data["system"]["name"] == "My System"
        assert len(data["projects"]) == 2
        assert len(data["edges"]) == 1
        # Check per-project run/goal data
        assert data["projects"][0]["last_run"] is None
        assert data["projects"][0]["next_queued_goal"] is not None
        assert data["projects"][1]["last_run"] is not None
        assert data["projects"][1]["next_queued_goal"] is None
        assert data["assembly_status"] is None

    @patch("psycopg.connect")
    def test_404_on_missing_system(self, mock_connect, client_raw):
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = None
        mock_conn = mock_connect.return_value.__enter__.return_value
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        resp = client_raw.get("/system/nonexistent")
        assert resp.status_code == 404

    @patch("psycopg.connect")
    def test_includes_assembly_status(self, mock_connect, client):
        mock_cur = MagicMock()
        mock_cur.fetchone.side_effect = [
            {"system_id": "sys-abc", "name": "My System", "description": ""},
            None, None,  # p1 run + goal
            None, None,  # p2 run + goal
            # assembly row found
            {"project_id": "asm1", "name": "Assembly"},
            # assembly last run
            {"id": "run-asm", "state": "done", "worktree_status": "merged",
             "created_at": "2026-01-01"},
        ]
        mock_cur.fetchall.side_effect = [
            [  # projects
                {"project_id": "p1", "name": "API", "kind": "component",
                 "status": "active", "description": ""},
                {"project_id": "asm1", "name": "Assembly", "kind": "assembly",
                 "status": "active", "description": ""},
            ],
            [],  # edges
        ]

        mock_conn = mock_connect.return_value.__enter__.return_value
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        resp = client.get("/system/sys-abc")
        data = resp.json()
        assert data["assembly_status"] is not None
        assert data["assembly_status"]["project_name"] == "Assembly"


# ── POST /system/{system_id}/assemble ───────────────────────────────────────

class TestAssembleSystem:
    @patch("backend.assembly.generator.generate_assembly")
    @patch("backend.assembly.generator.is_assembly_eligible")
    @patch("backend.assembly.generator.check_compose_valid")
    @patch("services.planner.main.os.environ", {"WORKSPACE_ROOT": "/tmp/ws"})
    def test_generates_assembly(self, mock_check, mock_eligible, mock_gen, client):
        mock_eligible.return_value = (True, "")
        mock_gen.return_value = {
            "compose_path": "/tmp/ws/sys-abc/docker-compose.yml",
            "compose_yaml": "version: '3'",
            "services": [
                {"slug": "api", "template": {"port": 8000}},
            ],
            "dockerfiles": {},
            "errors": [],
            "warnings": [],
            "project_ids": ["p1", "p2"],
        }
        mock_check.return_value = (True, "")

        resp = client.post("/system/sys-abc/assemble", json={})

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "assembled"
        assert "compose_yaml" in data
        assert data["service_count"] == 1

    @patch("backend.assembly.generator.is_assembly_eligible")
    def test_429_when_not_eligible(self, mock_eligible, client_raw):
        mock_eligible.return_value = (False, "No assembly project found")

        resp = client_raw.post("/system/sys-abc/assemble", json={})
        assert resp.status_code == 429

    @patch("backend.assembly.generator.generate_assembly")
    @patch("backend.assembly.generator.is_assembly_eligible")
    @patch("backend.assembly.generator.check_compose_valid")
    @patch("services.planner.main.os.environ", {"WORKSPACE_ROOT": "/tmp/ws"})
    def test_returns_errors(self, mock_check, mock_eligible, mock_gen, client):
        mock_eligible.return_value = (True, "")
        mock_gen.return_value = {
            "compose_path": "",
            "compose_yaml": "",
            "services": [],
            "dockerfiles": {},
            "errors": ["Port conflict on 8080"],
            "warnings": [],
            "project_ids": [],
        }
        mock_check.return_value = (False, "Port conflict on 8080")

        resp = client.post("/system/sys-abc/assemble", json={})
        assert resp.status_code == 500  # errors trigger 500
        assert "Port conflict" in resp.json().get("error", "")


# ── POST /system/{system_id}/projects ───────────────────────────────────────

class TestAddProjectToSystem:
    @patch("backend.assembly.proposal.propose_project")
    @patch("psycopg.connect")
    def test_proposes_project(self, mock_connect, mock_propose, client):
        """Valid system → propose_project called with spec/quality_intent/depends_on."""
        mock_cur = MagicMock()
        mock_cur.fetchone.side_effect = [(1,), (1,)]  # system + depends_on exist
        mock_conn = mock_connect.return_value.__enter__.return_value
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_propose.return_value = {"id": 7, "status": "proposed"}

        resp = client.post("/system/sys-abc/projects", json={
            "project_name": "billing",
            "kind": "service",
            "goal": "Build billing with enough characters for planning",
            "spec": "REST API with /invoices endpoint",
            "quality_intent": "High reliability",
            "depends_on": ["sys-abc-core"],
        })

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "proposed"
        assert data["intent_id"] == 7
        assert data["system_id"] == "sys-abc"
        mock_propose.assert_called_once_with(
            system_id="sys-abc",
            project_name="billing",
            kind="service",
            intent_text="Build billing with enough characters for planning",
            spec="REST API with /invoices endpoint",
            quality_intent="High reliability",
            depends_on=["sys-abc-core"],
        )

    @patch("psycopg.connect")
    def test_404_on_missing_system(self, mock_connect, client_raw):
        """Unknown system → 404 before propose_project is reached."""
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = None
        mock_conn = mock_connect.return_value.__enter__.return_value
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        resp = client_raw.post("/system/nope/projects", json={
            "project_name": "billing",
        })

        assert resp.status_code == 404
        assert "not found" in resp.json()["error"]

    @patch("psycopg.connect")
    def test_400_on_missing_dependency(self, mock_connect, client_raw):
        """depends_on referencing a project outside the system → 400."""
        mock_cur = MagicMock()
        mock_cur.fetchone.side_effect = [(1,), None]  # system exists, dep missing
        mock_conn = mock_connect.return_value.__enter__.return_value
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        resp = client_raw.post("/system/sys-abc/projects", json={
            "project_name": "billing",
            "depends_on": ["other-system-proj"],
        })

        assert resp.status_code == 400
        assert "other-system-proj" in resp.json()["error"]
