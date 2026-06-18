"""Tests for File 03: L2 rubric judge + preset library + Langfuse scoring.

[GATE 03]
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from backend.evaluator.schema import Check
from backend.evaluator.l2_judge import (
    L2Result,
    collect_artifact,
    run_l2,
    JUDGE_SYSTEM_PROMPT,
)
from backend.evaluator.gate import evaluate_gate


# ── Mock LLM ────────────────────────────────────────────────────────────────

def _mock_judge_pass(prompt: str) -> str:
    """Mock LLM that returns criteria_met=true for all rubric items."""
    return json.dumps({"criteria_met": True, "explanation": "All criteria satisfied."})


def _mock_judge_fail(prompt: str) -> str:
    """Mock LLM that returns criteria_met=false."""
    return json.dumps({"criteria_met": False, "explanation": "Missing error handling for edge cases."})


def _mock_judge_mixed(prompt: str) -> str:
    """Mock LLM that passes some items and fails others based on keyword."""
    if "edge" in prompt.lower() or "error" in prompt.lower():
        return json.dumps({"criteria_met": False, "explanation": "Edge cases not handled."})
    return json.dumps({"criteria_met": True, "explanation": "Looks good."})


def _mock_judge_malformed(prompt: str) -> str:
    """Mock LLM that returns unparseable output."""
    return "I think it's good."


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_worktree_with_diff():
    """Create a temp worktree with git init + a change to produce a diff."""
    d = Path(tempfile.mkdtemp(prefix="l2_test_"))
    # Init git repo
    import subprocess
    subprocess.run(["git", "init"], cwd=str(d), capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(d), capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(d), capture_output=True)

    # Initial commit
    (d / "README.md").write_text("# Test")
    subprocess.run(["git", "add", "-A"], cwd=str(d), capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(d), capture_output=True)

    # Make an uncommitted change (the "artifact")
    (d / "app.py").write_text("x = 1\ny = 2\n")
    yield str(d)
    import shutil
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def rubric_checks():
    return [
        Check(id="rubric-edge-cases", type="rubric", criterion="Edge cases handled",
              rubric_item="Are edge cases and error states handled?", weight=1.0),
        Check(id="rubric-code-quality", type="rubric", criterion="Code quality",
              rubric_item="Is the code idiomatic and well-structured?", weight=1.0),
    ]


@pytest.fixture
def mixed_checks():
    """Checks where some pass and some fail based on keyword."""
    return [
        Check(id="rubric-edge", type="rubric", criterion="Edge cases",
              rubric_item="Are edge cases and error states handled?", weight=2.0),
        Check(id="rubric-clarity", type="rubric", criterion="Clarity",
              rubric_item="Is the code clear and readable?", weight=1.0),
    ]


# ── Artifact Collection Tests ───────────────────────────────────────────────

class TestCollectArtifact:
    """Artifact collection from worktree."""

    def test_collect_artifact_returns_string(self, tmp_worktree_with_diff):
        artifact = collect_artifact(tmp_worktree_with_diff)
        assert isinstance(artifact, str)
        assert len(artifact) > 0

    def test_collect_artifact_contains_evidence(self, tmp_worktree_with_diff):
        artifact = collect_artifact(tmp_worktree_with_diff)
        # Either git diff or new files or both should be present
        assert any(kw in artifact.lower() for kw in ("git diff", "new files", "--- app.py ---"))

    def test_collect_artifact_empty_worktree(self):
        """Empty directory still returns a string."""
        with tempfile.TemporaryDirectory() as d:
            artifact = collect_artifact(d)
            assert isinstance(artifact, str)


# ── L2 Judge Tests ──────────────────────────────────────────────────────────

class TestRunL2:
    """1. run_l2 returns score with per-item judgments."""

    def test_all_rubrics_pass(self, rubric_checks, tmp_worktree_with_diff):
        """Good node → run_l2 returns high score with per-item results."""
        result = run_l2(rubric_checks, tmp_worktree_with_diff, llm_call=_mock_judge_pass)
        assert result.score >= 0.7
        assert len(result.judgments) == 2
        assert all(j.criteria_met for j in result.judgments)

    def test_no_rubric_checks_passes_vacuously(self, tmp_worktree_with_diff):
        """No rubric checks → vacuous pass with score 1.0."""
        result = run_l2([], tmp_worktree_with_diff, llm_call=_mock_judge_pass)
        assert result.score == 1.0
        assert result.judgments == []

    def test_only_deterministic_checks_passes_vacuously(self, tmp_worktree_with_diff):
        det_only = [
            Check(id="det-test", type="deterministic", criterion="Tests",
                  check_cmd="true"),
        ]
        result = run_l2(det_only, tmp_worktree_with_diff, llm_call=_mock_judge_pass)
        assert result.score == 1.0
        assert result.judgments == []

    def test_partial_pass_returns_mid_score(self, mixed_checks, tmp_worktree_with_diff):
        """Mixed results: some pass, some fail → score between 0 and 1."""
        result = run_l2(mixed_checks, tmp_worktree_with_diff, llm_call=_mock_judge_mixed)
        # 2 weight passes (clarity=1.0), 0 weight fails (edge=2.0) → met=1, total=3 → score=0.33
        assert 0.2 < result.score < 0.8
        assert result.items_met == 1  # only clarity passed

    def test_all_fail_returns_low_score(self, rubric_checks, tmp_worktree_with_diff):
        result = run_l2(rubric_checks, tmp_worktree_with_diff, llm_call=_mock_judge_fail)
        assert result.score == 0.0
        assert not any(j.criteria_met for j in result.judgments)

    def test_malformed_judge_output_handled(self, rubric_checks, tmp_worktree_with_diff):
        """Judge returns unparseable → criteria_met=False per item."""
        result = run_l2(rubric_checks, tmp_worktree_with_diff, llm_call=_mock_judge_malformed)
        assert result.score == 0.0
        assert all(not j.criteria_met for j in result.judgments)
        assert any("unparseable" in j.explanation.lower() for j in result.judgments)

    def test_weighted_scoring(self, tmp_worktree_with_diff):
        """Higher weight items have more impact on score."""
        uneven = [
            Check(id="rubric-heavy", type="rubric", criterion="Important",
                  rubric_item="Is the critical feature implemented?", weight=5.0),
            Check(id="rubric-light", type="rubric", criterion="Minor",
                  rubric_item="Is the formatting nice?", weight=1.0),
        ]
        # Fail important, pass minor
        def _mock_fail_important(prompt: str) -> str:
            if "critical" in prompt.lower():
                return json.dumps({"criteria_met": False, "explanation": "Not implemented."})
            return json.dumps({"criteria_met": True, "explanation": "Looks fine."})

        result = run_l2(uneven, tmp_worktree_with_diff, llm_call=_mock_fail_important)
        # total_weight=6, met=1 (light=1.0) → score=1/6=0.166
        assert result.score == pytest.approx(1.0 / 6.0, abs=0.01)

    def test_judgment_explanations_are_strings(self, rubric_checks, tmp_worktree_with_diff):
        result = run_l2(rubric_checks, tmp_worktree_with_diff, llm_call=_mock_judge_pass)
        for j in result.judgments:
            assert isinstance(j.explanation, str)
            assert len(j.explanation) > 0


# ── Gate Integration Tests (L1 + L2) ────────────────────────────────────────

class TestGateWithL2:
    """2. Gate with L2: L1 fail → remediate, L1 pass → L2 → threshold gate."""

    def test_l1_pass_l2_pass_advances(self, tmp_worktree_with_diff):
        checks = [
            Check(id="rubric-quality", type="rubric", criterion="Quality",
                  rubric_item="Is the code quality good?"),
        ]
        decision = evaluate_gate(
            checks, tmp_worktree_with_diff,
            l2_fn=lambda c, w: run_l2(c, w, llm_call=_mock_judge_pass),
            threshold=0.7,
        )
        assert decision.action == "advance"

    def test_l1_pass_l2_fail_remediates(self, tmp_worktree_with_diff):
        """2. L1 passes, L2 catches quality issue L1 missed → remediate."""
        checks = [
            Check(id="det-true", type="deterministic", criterion="Noop",
                  check_cmd="true"),
            Check(id="rubric-quality", type="rubric", criterion="Quality",
                  rubric_item="Is the code free of security issues?"),
        ]
        decision = evaluate_gate(
            checks, tmp_worktree_with_diff,
            l2_fn=lambda c, w: run_l2(c, w, llm_call=_mock_judge_fail),
            threshold=0.7,
        )
        assert decision.action == "remediate"
        assert decision.reason.get("layer") == "L2"

    def test_l1_fail_skips_l2(self, tmp_worktree_with_diff):
        """L1 fail → remediate before L2 runs (saves tokens)."""
        checks = [
            Check(id="det-fail", type="deterministic", criterion="Fails",
                  check_cmd="false"),
            Check(id="rubric-quality", type="rubric", criterion="Quality",
                  rubric_item="Is it good?"),
        ]
        decision = evaluate_gate(
            checks, tmp_worktree_with_diff,
            l2_fn=lambda c, w: (_ for _ in ()).throw(Exception("should not be called")),
            threshold=0.7,
        )
        assert decision.action == "remediate"
        assert decision.reason.get("layer") == "L1"

    def test_existing_gate_tests_still_pass(self, tmp_worktree_with_diff):
        """Gate without l2_fn still works as before (backward compat)."""
        from backend.evaluator.schema import Check
        from backend.evaluator.gate import evaluate_gate
        decision = evaluate_gate([], tmp_worktree_with_diff)
        assert decision.action == "advance"


# ── Preset Selection Tests ───────────────────────────────────────────────────

class TestRubricPresets:
    """3. Preset selection based on node type."""

    def test_crud_preset_applied_for_backend_tasks(self):
        """executor member pulls code_implementation rubric items."""
        from backend.evaluator.generate import generate_checks
        nc = generate_checks(
            node_id="node-1",
            task="Build CRUD API for user management",
            success_criterion="All CRUD endpoints work with proper error handling",
            node_index=0,
            total_nodes=1,
            members=["opencode:backend-executor"],
        )
        rubric_ids = {c.id for c in nc.checks if c.type == "rubric"}
        assert "correctness" in rubric_ids
        assert "error_handling" in rubric_ids
        assert "matches_spec" in rubric_ids

    def test_default_preset_for_unknown_type(self):
        """No members -> generic_quality fallback."""
        from backend.evaluator.generate import generate_checks
        nc = generate_checks(
            node_id="node-1",
            task="Random task",
            success_criterion="Do something",
            node_index=0,
            total_nodes=1,
        )
        rubric_ids = {c.id for c in nc.checks if c.type == "rubric"}
        assert "meets_goal" in rubric_ids
        assert "complete" in rubric_ids
        assert "quality" in rubric_ids
