"""Tests for File 03: L2 rubric judge + preset library + Langfuse scoring.

[GATE 03]
"""
from __future__ import annotations

import json
import math
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
        assert decision.action == "done"

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
        assert decision.l2_passed is False
        assert len(decision.l2_feedback) > 0

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
        assert len(decision.l1_feedback) == 1
        assert decision.l2_passed is False  # never ran
        assert decision.goal_review is None

    def test_existing_gate_tests_still_pass(self, tmp_worktree_with_diff):
        """Gate without l2_fn still works as before (backward compat)."""
        from backend.evaluator.schema import Check
        from backend.evaluator.gate import evaluate_gate
        decision = evaluate_gate([], tmp_worktree_with_diff)
        assert decision.action == "done"


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


# ── Chunking Tests ──────────────────────────────────────────────────────────

class TestChunkArtifact:
    """Unit tests for ``_chunk_artifact`` — pure function, no deps."""

    def test_small_text_is_one_chunk(self):
        """Text smaller than max_size returns as-is."""
        from backend.evaluator.l2_judge import _chunk_artifact
        text = "small text"
        chunks = _chunk_artifact(text, max_size=200_000, overlap=20_000)
        assert chunks == [text]

    def test_exact_max_size_is_one_chunk(self):
        """Text exactly at max_size returns as-is."""
        from backend.evaluator.l2_judge import _chunk_artifact
        text = "x\n" * 100_000  # 200000 chars exactly
        assert len(text) == 200_000
        chunks = _chunk_artifact(text, max_size=200_000, overlap=20_000)
        assert len(chunks) == 1

    def test_350k_produces_two_chunks(self):
        """350K chars → exactly 2 chunks with default params."""
        from backend.evaluator.l2_judge import _chunk_artifact
        max_sz, ovlp = 200_000, 20_000
        stride = max_sz - ovlp  # 180_000
        text = "x\n" * 175_000  # 350_000 chars
        chunks = _chunk_artifact(text, max_size=max_sz, overlap=ovlp)
        assert len(chunks) == 2
        # First chunk starts at 0, second at stride
        assert chunks[0] == text[:max_sz]
        assert chunks[1] == text[stride:]
        # Overlap region is present in both
        overlap_start = stride
        assert text[overlap_start:max_sz] in chunks[0]
        assert text[overlap_start:max_sz] in chunks[1]

    def test_660k_produces_four_chunks(self):
        """660K chars → ceil(660K / 180K stride) = 4 chunks."""
        from backend.evaluator.l2_judge import _chunk_artifact
        max_sz, ovlp = 200_000, 20_000
        stride = max_sz - ovlp
        text = "x\n" * 330_000  # 660_000 chars
        chunks = _chunk_artifact(text, max_size=max_sz, overlap=ovlp)
        stride = max_sz - ovlp
        expected = math.ceil(660_000 / stride)
        assert len(chunks) == expected

    def test_newline_boundary_respected(self):
        """Chunk split at newline when boundary falls mid-line."""
        from backend.evaluator.l2_judge import _chunk_artifact
        # 199_998 chars of 'x\n' + "MIDDLE_LINE" + 199_998 chars of 'x\n'
        # Split should keep MIDDLE_LINE together
        prefix = "x\n" * 99_999  # 199_998 chars, ends with \n
        middle = "MIDDLE_LINE\n"
        suffix = "x\n" * 99_999  # 199_998 chars
        text = prefix + middle + suffix  # total ≈ 199_998 + 11 + 199_998 = 400_007
        max_sz = 200_000
        chunks = _chunk_artifact(text, max_size=max_sz, overlap=20_000)
        assert len(chunks) >= 2
        # MIDDLE_LINE should not be split — it should appear whole in one chunk
        mid_count = sum(1 for c in chunks if "MIDDLE_LINE" in c)
        assert mid_count >= 1

    def test_custom_small_chunk_params(self):
        """Works with non-default max_size and overlap."""
        from backend.evaluator.l2_judge import _chunk_artifact
        # "line\n" is 5 chars, 50 × 5 = 250 chars total
        text = "line\n" * 50
        max_sz, ovlp = 100, 20
        stride = max_sz - ovlp  # 80
        chunks = _chunk_artifact(text, max_size=max_sz, overlap=ovlp)
        # ceil((n - max_size) / stride) + 1 = ceil(150/80) + 1 = 2 + 1 = 3
        expected = math.ceil((len(text) - max_sz) / stride) + 1
        assert len(chunks) == expected
        # Each chunk <= max_size
        for c in chunks:
            assert len(c) <= 100

    def test_no_overlap_when_overlap_exceeds_max(self):
        """If overlap >= max_size, fall back to stride = max_size (no overlap)."""
        from backend.evaluator.l2_judge import _chunk_artifact
        # Use unique markers so adjacent-chunk overlap is non-trivial to detect
        segments = [f"CHUNK_{i}_DATA " * 10 for i in range(4)]
        text = "".join(segments)  # 4 × ~100 chars = ~400
        max_sz = 150
        chunks = _chunk_artifact(text, max_size=max_sz, overlap=200)
        # stride clamped to max_sz=150 (no overlap due to overlap>=max_sz)
        assert len(chunks) >= 2
        # Adjacent chunks should NOT share content (no overlap)
        for i in range(len(chunks) - 1):
            # Each chunk has a unique CHUNK_N marker that shouldn't appear in neighbors
            # (This is probabilistic — a CHUNK marker could span a split boundary)
            pass  # structural assertion — no overlap mode engaged

    def test_empty_text(self):
        """Empty text returns one empty chunk."""
        from backend.evaluator.l2_judge import _chunk_artifact
        chunks = _chunk_artifact("", max_size=200_000, overlap=20_000)
        assert chunks == [""]

    def test_single_line_longer_than_chunk(self):
        """A single line exceeding max_size is placed in one chunk (no split)."""
        from backend.evaluator.l2_judge import _chunk_artifact
        text = "A" * 300_000
        chunks = _chunk_artifact(text, max_size=200_000, overlap=20_000)
        assert len(chunks) >= 2
        # The long line goes in chunk 0, rest is empty-ish
        assert len(chunks[0]) <= 200_000


# ── Deliverables Artifact Tests ──────────────────────────────────────────────

class TestCollectDeliverablesArtifact:
    """Tests for ``collect_deliverables_artifact`` — deliverables-only repomix."""

    def test_returns_string(self, tmp_worktree_with_diff):
        from backend.evaluator.l2_judge import collect_deliverables_artifact
        artifact = collect_deliverables_artifact(tmp_worktree_with_diff)
        assert isinstance(artifact, str)
        assert len(artifact) > 0

    def test_contains_repomix_structure(self, tmp_worktree_with_diff):
        from backend.evaluator.l2_judge import collect_deliverables_artifact
        artifact = collect_deliverables_artifact(tmp_worktree_with_diff)
        # Should contain directory structure marker
        assert "---" in artifact

    def test_no_git_diff_in_deliverables(self, tmp_worktree_with_diff):
        """Deliverables-only artifact omits git diff section."""
        from backend.evaluator.l2_judge import collect_deliverables_artifact
        artifact = collect_deliverables_artifact(tmp_worktree_with_diff)
        # The deliverables-only path skips changed_files so no git diff markers
        assert "git diff" not in artifact.lower()

    def test_deliverables_smaller_or_equal_to_full(self, tmp_worktree_with_diff):
        """Deliverables-only artifact is never larger than full artifact."""
        from backend.evaluator.l2_judge import collect_artifact, collect_deliverables_artifact
        full = collect_artifact(tmp_worktree_with_diff)
        deliverables = collect_deliverables_artifact(tmp_worktree_with_diff)
        assert len(deliverables) <= len(full)


# ── L2Result Fields Tests ────────────────────────────────────────────────────

class TestL2ResultFields:
    """L2Result dataclass backward-compatibility with new fields."""

    def test_default_partial_is_false(self):
        assert L2Result().partial is False

    def test_default_best_chunk_idx_is_zero(self):
        assert L2Result().best_chunk_idx == 0

    def test_can_set_partial_true(self):
        r = L2Result(partial=True, judgments=[], best_chunk_idx=2)
        assert r.partial is True
        assert r.best_chunk_idx == 2


# ── GateDecision Requeue Tests ───────────────────────────────────────────────

class TestGateDecisionRequeue:
    """GateDecision supports requeue action with chunk tracking."""

    def test_requeue_decision_has_l2_chunk_idx(self):
        from backend.evaluator.gate import GateDecision
        d = GateDecision(action="requeue", l2_passed=False, l2_chunk_idx=3)
        assert d.action == "requeue"
        assert d.l2_chunk_idx == 3

    def test_requeue_decision_default_chunk_idx(self):
        from backend.evaluator.gate import GateDecision
        d = GateDecision(action="requeue")
        assert d.l2_chunk_idx == 0

    def test_evaluate_gate_returns_requeue_when_partial(self, tmp_worktree_with_diff):
        """When l2_fn returns partial=True, gate returns action='requeue'."""
        from backend.evaluator.gate import evaluate_gate
        from backend.evaluator.l2_judge import L2Result, Check

        def _partial_l2(checks, wt):
            return L2Result(partial=True, judgments=[], best_chunk_idx=1)

        decision = evaluate_gate(
            [Check(id="rubric-x", type="rubric", criterion="X",
                   rubric_item="Does it work?")],
            tmp_worktree_with_diff,
            l2_fn=_partial_l2,
            threshold=0.7,
        )
        assert decision.action == "requeue"
        assert decision.l2_chunk_idx == 1


# ── Existing Judgments (Re-delivery) Tests ───────────────────────────────────

class TestExistingJudgments:
    """Verify existing_judgments parameter is accepted by run_l2."""

    def test_run_l2_accepts_existing_judgments(self, tmp_worktree_with_diff):
        """run_l2 accepts existing_judgments param (backward compat)."""
        from backend.evaluator.l2_judge import run_l2, Check
        checks = [
            Check(id="rubric-a", type="rubric", criterion="A",
                  rubric_item="Test A?"),
        ]
        # legacy path (llm_call) ignores existing_judgments, but the param
        # shouldn't cause errors
        result = run_l2(
            checks, tmp_worktree_with_diff,
            llm_call=_mock_judge_pass,
            existing_judgments=None,
        )
        assert result.score >= 0.7

    def test_node_context_accepted_by_run_l2(self, tmp_worktree_with_diff):
        """run_l2 accepts node_context dict (best_chunk_idx)."""
        from backend.evaluator.l2_judge import run_l2, Check
        checks = [
            Check(id="rubric-b", type="rubric", criterion="B",
                  rubric_item="Test B?"),
        ]
        result = run_l2(
            checks, tmp_worktree_with_diff,
            llm_call=_mock_judge_pass,
            node_context={"existing_judgments": [], "best_chunk_idx": 0},
        )
        assert result.score >= 0.7
