from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from services.planner.main import _validate_supplied_dag_dict

# ── Test data ─────────────────────────────────────────────────────────

HAPPY_DAG = [
    {
        "id": "node-1",
        "members": [
            {"agent_config": "opencode:backend-executor", "backend": "opencode"},
        ],
        "depends_on": [],
        "task": {
            "text": "Implement health check endpoint",
            "inputs": [],
            "deliverables": ["GET /health returns 200"],
        },
        "success": {"text": "Health endpoint returns 200 OK"},
        "capabilities": ["backend_api", "cli_tool"],
        "checks": [
            {
                "id": "l1-file-exists",
                "type": "deterministic",
                "criterion": "Health check module exists",
                "check_cmd": "test -f app.py",
            },
            {
                "id": "l2-quality",
                "type": "rubric",
                "criterion": "Code is well-structured",
                "rubric_item": "Is the code idiomatic and well-structured?",
            },
        ],
    },
    {
        "id": "node-2",
        "members": [
            {"agent_config": "opencode:backend-executor", "backend": "opencode"},
        ],
        "depends_on": ["node-1"],
        "task": {
            "text": "Add database integration layer",
            "inputs": [],
            "deliverables": ["DB connection pool", "User model"],
        },
        "success": {"text": "Database integration is working"},
        "capabilities": ["backend_api"],
        "checks": [
            {
                "id": "l1-db-module",
                "type": "deterministic",
                "criterion": "Database module exists",
                "check_cmd": "test -f db.py",
            },
        ],
    },
]


# ── Happy path: valid BYO-DAG through the API ─────────────────────────


class TestBYODAGHappyPath:
    """BYO-DAG through the HTTP API with valid inputs."""

    def test_happy_path_returns_gated_ok(
        self,
        client: TestClient,
        mock_gate_plan,
        mock_save_plan,
    ):
        resp = client.post(
            "/goal",
            json={
                "raw_input": "Build a backend service with health check and DB",
                "project_id": "test",
                "nodes": HAPPY_DAG,
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "gated_ok"
        assert body["plan_id"].startswith("plan_")
        assert body["plan_goal_review"] == 0.85
        assert body["error"] is None

    def test_skips_langgraph_when_nodes_provided(
        self,
        client: TestClient,
        mock_gate_plan,
        mock_save_plan,
    ):
        with patch("services.planner.main.planner_graph.invoke") as mock_invoke:
            resp = client.post(
                "/goal",
                json={
                    "raw_input": "test",
                    "project_id": "test",
                    "nodes": HAPPY_DAG,
                },
            )
        assert resp.status_code == 200
        mock_invoke.assert_not_called()

    def test_capabilities_preserved_through_gate(
        self,
        client: TestClient,
        mock_gate_plan,
        mock_save_plan,
    ):
        client.post(
            "/goal",
            json={
                "raw_input": "test",
                "project_id": "test",
                "nodes": HAPPY_DAG,
            },
        )
        dag_arg = mock_gate_plan.call_args[0][0]
        assert dag_arg[0]["capabilities"] == ["backend_api", "cli_tool"]
        assert dag_arg[1]["capabilities"] == ["backend_api"]

    def test_checks_preserved_when_provided(
        self,
        client: TestClient,
        mock_gate_plan,
        mock_save_plan,
    ):
        """Pre-set checks survive through to gate_plan check conversion."""
        client.post(
            "/goal",
            json={
                "raw_input": "test",
                "project_id": "test",
                "nodes": HAPPY_DAG,
            },
        )
        dag_arg = mock_gate_plan.call_args[0][0]
        node1_checks = dag_arg[0]["checks"]
        assert len(node1_checks) == 2
        check_ids = {c["id"] for c in node1_checks}
        assert "l1-file-exists" in check_ids
        assert "l2-quality" in check_ids

    def test_save_plan_called_with_matching_id(
        self,
        client: TestClient,
        mock_gate_plan,
        mock_save_plan,
    ):
        resp = client.post(
            "/goal",
            json={
                "raw_input": "test",
                "project_id": "test-project",
                "nodes": HAPPY_DAG,
            },
        )
        plan_id = resp.json()["plan_id"]
        save_call = mock_save_plan.call_args
        saved_plan = save_call[1]["plan"]
        assert saved_plan.plan_id == plan_id
        assert saved_plan.project_id == "test-project"
        assert len(saved_plan.dag) == 2

    def test_byo_dag_without_nodes_uses_langgraph(
        self,
        client: TestClient,
    ):
        """When nodes is not provided, the LangGraph path runs."""
        with patch(
            "services.planner.main.planner_graph.invoke",
        ) as mock_invoke:
            mock_invoke.return_value = {
                "status": "gated_ok",
                "meta_goal": {"goal": "test goal"},
                "dag": HAPPY_DAG,
                "plan_goal_review": None,
                "error": None,
            }
            with patch("backend.planning.store.save_plan"):
                resp = client.post(
                    "/goal",
                    json={
                        "raw_input": "test",
                        "project_id": "test",
                    },
                )
        assert resp.status_code == 200
        mock_invoke.assert_called_once()


# ── Check generation behavior ────────────────────────────────────────


class TestBYODAGCheckGeneration:
    """When pre-set checks are absent, checkgen should be invoked."""

    def test_triggers_checkgen_for_nodes_without_checks(
        self,
        client: TestClient,
        mock_gate_plan,
        mock_save_plan,
        mock_checkgen,
    ):
        dag_no_checks = [{**n, "checks": []} for n in HAPPY_DAG]
        client.post(
            "/goal",
            json={
                "raw_input": "test",
                "project_id": "test",
                "nodes": dag_no_checks,
            },
        )
        assert mock_checkgen.call_count >= 2

    def test_triggers_checkgen_for_empty_capabilities(
        self,
        client: TestClient,
        mock_gate_plan,
        mock_save_plan,
        mock_checkgen,
    ):
        dag_no_caps = [{**n, "capabilities": [], "checks": []} for n in HAPPY_DAG]
        client.post(
            "/goal",
            json={
                "raw_input": "test",
                "project_id": "test",
                "nodes": dag_no_caps,
            },
        )
        assert mock_checkgen.call_count >= 2

    def test_skips_checkgen_when_all_checks_present(
        self,
        client: TestClient,
        mock_gate_plan,
        mock_save_plan,
        mock_checkgen,
    ):
        """With pre-set checks on every node, checkgen should not be called."""
        client.post(
            "/goal",
            json={
                "raw_input": "test",
                "project_id": "test",
                "nodes": HAPPY_DAG,
            },
        )
        assert mock_checkgen.call_count == 0


# ── API-level validation errors ──────────────────────────────────────


class TestBYODAGValidationAPI:
    """Malformed DAGs returned as HTTP 500 via the API."""

    def test_missing_backend(self, client_raw: TestClient):
        dag = [
            {
                "id": "node-1",
                "members": [
                    {"agent_config": "opencode:backend-executor"},
                ],
                "task": {"text": "Do work", "inputs": [], "deliverables": []},
                "success": {"text": "Done"},
            },
        ]
        resp = client_raw.post(
            "/goal",
            json={"raw_input": "test", "project_id": "test", "nodes": dag},
        )
        assert resp.status_code == 500

    def test_duplicate_ids(self, client_raw: TestClient):
        dag = [
            {
                "id": "node-1",
                "members": [
                    {"agent_config": "ac", "backend": "opencode"},
                ],
                "task": {"text": "Task A", "inputs": [], "deliverables": []},
                "success": {"text": "Done"},
            },
            {
                "id": "node-1",
                "members": [
                    {"agent_config": "ac", "backend": "opencode"},
                ],
                "task": {"text": "Task B", "inputs": [], "deliverables": []},
                "success": {"text": "Done"},
            },
        ]
        resp = client_raw.post(
            "/goal",
            json={"raw_input": "test", "project_id": "test", "nodes": dag},
        )
        assert resp.status_code == 500

    def test_cycle(self, client_raw: TestClient):
        dag = [
            {
                "id": "node-1",
                "members": [
                    {"agent_config": "ac", "backend": "opencode"},
                ],
                "depends_on": ["node-2"],
                "task": {"text": "A", "inputs": [], "deliverables": []},
                "success": {"text": "Done"},
            },
            {
                "id": "node-2",
                "members": [
                    {"agent_config": "ac", "backend": "opencode"},
                ],
                "depends_on": ["node-1"],
                "task": {"text": "B", "inputs": [], "deliverables": []},
                "success": {"text": "Done"},
            },
        ]
        resp = client_raw.post(
            "/goal",
            json={"raw_input": "test", "project_id": "test", "nodes": dag},
        )
        assert resp.status_code == 500

    def test_dep_not_found(self, client_raw: TestClient):
        dag = [
            {
                "id": "node-1",
                "members": [
                    {"agent_config": "ac", "backend": "opencode"},
                ],
                "depends_on": ["nonexistent-node"],
                "task": {"text": "A", "inputs": [], "deliverables": []},
                "success": {"text": "Done"},
            },
        ]
        resp = client_raw.post(
            "/goal",
            json={"raw_input": "test", "project_id": "test", "nodes": dag},
        )
        assert resp.status_code == 500

    def test_empty_members(self, client_raw: TestClient):
        dag = [
            {
                "id": "node-1",
                "members": [],
                "task": {"text": "A", "inputs": [], "deliverables": []},
                "success": {"text": "Done"},
            },
        ]
        resp = client_raw.post(
            "/goal",
            json={"raw_input": "test", "project_id": "test", "nodes": dag},
        )
        assert resp.status_code == 500


# ── Direct validation function tests ─────────────────────────────────


class TestBYODAGValidationDirect:
    """Precise error messages via direct call to _validate_supplied_dag_dict."""

    def test_missing_backend_message(self):
        dag = [
            {
                "id": "n1",
                "members": [{"agent_config": "ac"}],
            },
        ]
        with pytest.raises(ValueError, match="missing backend"):
            _validate_supplied_dag_dict(dag)

    def test_duplicate_id_message(self):
        dag = [
            {"id": "n1", "members": [{"agent_config": "ac", "backend": "oc"}]},
            {"id": "n1", "members": [{"agent_config": "ac", "backend": "oc"}]},
        ]
        with pytest.raises(ValueError, match="Duplicate node id"):
            _validate_supplied_dag_dict(dag)

    def test_no_members_message(self):
        dag = [
            {"id": "n1", "members": None},
        ]
        with pytest.raises(ValueError, match="at least one member"):
            _validate_supplied_dag_dict(dag)

    def test_empty_members_list_message(self):
        dag = [
            {"id": "n1", "members": []},
        ]
        with pytest.raises(ValueError, match="at least one member"):
            _validate_supplied_dag_dict(dag)

    def test_cycle_message(self):
        dag = [
            {
                "id": "n1",
                "depends_on": ["n2"],
                "members": [{"agent_config": "ac", "backend": "oc"}],
            },
            {
                "id": "n2",
                "depends_on": ["n1"],
                "members": [{"agent_config": "ac", "backend": "oc"}],
            },
        ]
        with pytest.raises(ValueError, match="Cycle detected"):
            _validate_supplied_dag_dict(dag)

    def test_dep_not_found_message(self):
        dag = [
            {
                "id": "n1",
                "depends_on": ["ghost"],
                "members": [{"agent_config": "ac", "backend": "oc"}],
            },
        ]
        with pytest.raises(ValueError, match="not found in DAG"):
            _validate_supplied_dag_dict(dag)

    def test_valid_dag_passes_cleanly(self):
        dag = [
            {
                "id": "n1",
                "depends_on": [],
                "members": [{"agent_config": "ac", "backend": "oc"}],
                "capabilities": ["backend_api"],
            },
            {
                "id": "n2",
                "depends_on": ["n1"],
                "members": [{"agent_config": "ac", "backend": "oc"}],
                "capabilities": ["cli_tool"],
            },
        ]
        _validate_supplied_dag_dict(dag)

    def test_auto_id_fallback(self):
        """Nodes without an explicit id get an auto-generated one."""
        dag = [
            {
                "members": [{"agent_config": "ac", "backend": "oc"}],
                "capabilities": ["cap1"],
            },
            {
                "members": [{"agent_config": "ac", "backend": "oc"}],
                "capabilities": ["cap2"],
            },
        ]
        _validate_supplied_dag_dict(dag)
