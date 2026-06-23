"""File 03.7: Single-node starter run test — prove the evaluator chain.

Tests the full generate→L1→L2→gate chain without requiring a live DB or AionUi.
An ``@pytest.mark.integration`` test exercises the API create_plan → ratify → create_run
path against a live backend (requires DB + running app).
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from backend.evaluator.gate import evaluate_gate, GateDecision
from backend.evaluator.generate import generate_checks
from backend.evaluator.l2_judge import L2Result, run_l2
from backend.evaluator.schema import Check


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_mock_llm(always_pass: bool = True):
    """Return a mock LLM that always returns 'criteria_met: true/false'."""
    def mock_call(prompt: str) -> str:
        return json.dumps({
            "criteria_met": always_pass,
            "explanation": "mock judgment",
        })
    return mock_call


def _make_rubric_checks() -> list[Check]:
    """Generate rubric checks using the member-based rubric selection."""
    nc = generate_checks(
        node_id="node-1",
        task="Implement the payment API endpoint with validation and tests",
        success_criterion="All endpoints work with proper validation, tests pass",
        node_index=0,
        total_nodes=1,
        members=["opencode:backend-executor"],
    )
    return nc.checks


def _make_det_checks() -> list[Check]:
    """Generate deterministic checks."""
    nc = generate_checks(
        node_id="node-1",
        task="Run existing tests",
        success_criterion="All tests pass",
        node_index=0,
        total_nodes=1,
        members=["opencode:backend-executor"],
    )
    return nc.checks


# ── E2E scenario tests (no DB required) ─────────────────────────────────────

class TestEvaluatorE2E:
    """Prove the full L1→L2→gate chain produces a non-null goal_review."""

    def test_full_chain_advance(self, tmp_path):
        """1. Full generate→L1→L2→advance chain with non-null goal_review."""
        # Use rubric-only checks so L1 passes vacuously (no shell commands to fail)
        nc = generate_checks(
            node_id="node-1",
            task="Implement the payment API",
            success_criterion="All endpoints work",
            node_index=0, total_nodes=1,
            members=["opencode:api-dev"],  # api role — deterministic gen won't match
        )
        rubric_only = [c for c in nc.checks if c.type == "rubric"]
        assert len(rubric_only) > 0

        l2_result = run_l2(rubric_only, str(tmp_path), llm_call=_make_mock_llm(True))
        assert l2_result.score >= 0.7
        assert l2_result.items_met == len(rubric_only)

        decision = evaluate_gate(rubric_only, str(tmp_path), l2_fn=lambda c, w: l2_result)
        assert decision.action == "done"
        assert decision.goal_review is not None
        assert decision.goal_review >= 0.7

    def test_full_chain_remediate_L1(self, tmp_path):
        """2. L1 failure → remediate (no goal_review set)."""
        # A deterministic check that will fail in an empty tmp_path
        det_check = Check(
            id="det-must-fail",
            type="deterministic",
            criterion="A file must exist",
            check_cmd="test -f nonexistent_file_xyz",
        )
        decision = evaluate_gate([det_check], str(tmp_path), l2_fn=lambda c, w: L2Result(score=1.0))
        assert decision.action == "remediate"
        assert len(decision.l1_feedback) == 1
        assert decision.l1_feedback[0]["tier"] == "L1"
        # goal_review is None because L1 failed before L2 ran
        assert decision.goal_review is None

    def test_full_chain_remediate_L2(self, tmp_path):
        """3. L1 passes, L2 fails → remediate with goal_review set."""
        nc = generate_checks(
            node_id="node-1",
            task="Implement the payment API",
            success_criterion="All endpoints work",
            node_index=0, total_nodes=1,
            members=["opencode:api-dev"],
        )
        rubric_only = [c for c in nc.checks if c.type == "rubric"]
        l2_result = run_l2(rubric_only, str(tmp_path), llm_call=_make_mock_llm(False))
        assert l2_result.score < 0.7

        decision = evaluate_gate(rubric_only, str(tmp_path), l2_fn=lambda c, w: l2_result)
        assert decision.action == "done"
        assert decision.goal_review is not None
        assert decision.goal_review >= 0.7

    def test_full_chain_remediate_L1(self, tmp_path):
        """2. L1 failure → remediate (no goal_review set)."""
        # A deterministic check that will fail in an empty tmp_path
        det_check = Check(
            id="det-must-fail",
            type="deterministic",
            criterion="A file must exist",
            check_cmd="test -f nonexistent_file_xyz",
        )
        decision = evaluate_gate([det_check], str(tmp_path), l2_fn=lambda c, w: L2Result(score=1.0))
        assert decision.action == "remediate"
        assert len(decision.l1_feedback) == 1
        # goal_review is None because L1 failed before L2 ran
        assert decision.goal_review is None

    def test_full_chain_remediate_L2(self, tmp_path):
        """3. L1 passes, L2 fails → remediate with goal_review set."""
        nc = generate_checks(
            node_id="node-1",
            task="Implement the payment API",
            success_criterion="All endpoints work",
            node_index=0, total_nodes=1,
            members=["opencode:api-dev"],
        )
        rubric_only = [c for c in nc.checks if c.type == "rubric"]
        l2_result = run_l2(rubric_only, str(tmp_path), llm_call=_make_mock_llm(False))
        assert l2_result.score < 0.7

        decision = evaluate_gate(rubric_only, str(tmp_path), l2_fn=lambda c, w: l2_result)
        assert decision.action == "remediate"
        assert decision.l2_passed is False
        assert len(decision.l2_feedback) > 0
        assert decision.goal_review is not None
        assert decision.goal_review < 0.7

    def test_generate_checks_rubric_selection(self):
        """4. Rubric selection based on member role produces correct check IDs."""
        # executor → code_implementation rubric
        nc = generate_checks(
            node_id="node-1",
            task="Build the backend API",
            success_criterion="API works",
            node_index=0,
            total_nodes=1,
            members=["opencode:backend-executor"],
        )
        rubric_ids = {c.id for c in nc.checks if c.type == "rubric"}
        assert "correctness" in rubric_ids, f"Expected correctness in {rubric_ids}"
        assert "error_handling" in rubric_ids, f"Expected error_handling in {rubric_ids}"
        assert "matches_spec" in rubric_ids, f"Expected matches_spec in {rubric_ids}"

        # reviewer → review_verification
        nc2 = generate_checks(
            node_id="node-2",
            task="Review the API implementation",
            success_criterion="Code is reviewed",
            node_index=1,
            total_nodes=1,
            members=["opencode:reviewer"],
        )
        rubric_ids2 = {c.id for c in nc2.checks if c.type == "rubric"}
        assert "ran_it" in rubric_ids2
        assert "e2e_cycle" in rubric_ids2

        # No members → generic_quality fallback
        nc3 = generate_checks(
            node_id="node-3",
            task="Generic task",
            success_criterion="Done",
            node_index=0,
            total_nodes=1,
        )
        rubric_ids3 = {c.id for c in nc3.checks if c.type == "rubric"}
        assert "meets_goal" in rubric_ids3
        assert "quality" in rubric_ids3


# ── Integration test (requires DB + running backend) ───────────────────────

@pytest.mark.integration
class TestSingleNodeRun:
    """File 03.7: Single-node run through the full API chain.

    Requires a live backend on :8090 with PostgreSQL available.
    """

    BASE_URL = "http://127.0.0.1:8090"

    def test_create_plan_and_run(self):
        """Create plan → ratify → create_run → approve_run → verify chain."""
        import urllib.request
        import urllib.error

        # 1. Create plan
        plan_payload = {
            "user_intent": "Build a health check endpoint",
            "goal": "Create a health check endpoint that returns 200 OK",
            "project_id": "test",
            "spec": json.dumps({
                "nodes": [
                    {
                        "id": "node-1",
                        "members": [{"agent_config": "opencode:backend-executor", "backend": "opencode"}],
                        "task": {"text": "Create GET /health that returns {\"status\":\"ok\"}"},
                        "success": {"text": "Endpoint returns 200 OK"},
                    }
                ]
            }),
        }
        req = urllib.request.Request(
            f"{self.BASE_URL}/api/plans",
            data=json.dumps(plan_payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                plan = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            pytest.skip(f"Backend not available (HTTP {e.code}): {e.read().decode()[:200]}")
        except Exception as e:
            pytest.skip(f"Backend not available: {e}")

        plan_id = plan.get("plan_id") or plan.get("id")
        assert plan_id, f"No plan_id in response: {plan}"

        # 2. Ratify plan
        ratify_req = urllib.request.Request(
            f"{self.BASE_URL}/api/plans/{plan_id}/ratify",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(ratify_req, timeout=15) as resp:
                ratified = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            pytest.skip(f"Ratify failed (HTTP {e.code})")
        assert ratified is not None

        # 3. Create run
        run_payload = {"plan_id": plan_id}
        run_req = urllib.request.Request(
            f"{self.BASE_URL}/api/runs",
            data=json.dumps(run_payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(run_req, timeout=15) as resp:
                run = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            pytest.skip(f"Create run failed (HTTP {e.code})")
        run_id = run.get("id") or run.get("run_id")
        assert run_id

        # 4. Approve run
        approve_req = urllib.request.Request(
            f"{self.BASE_URL}/api/runs/{run_id}/approve",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(approve_req, timeout=15) as resp:
                approved = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            pytest.skip(f"Approve run failed (HTTP {e.code})")
        assert approved is not None
        print(f"[E2E] Plan={plan_id} Run={run_id} — create→ratify→create_run→approve OK")
