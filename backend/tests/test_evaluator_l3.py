"""Tests for File 05: L3 Meta-Evaluation — golden set, jury, calibration.

[GATE 05]
"""
from __future__ import annotations

import json
import sys

import pytest

from backend.evaluator.l3_meta.golden import GoldenItem, load_golden, add_golden, count_golden
from backend.evaluator.l3_meta.jury import jury_score
from backend.evaluator.l3_meta.metaeval import (
    measure_disagreement,
    propose_rubric_refinement,
    queue_for_approval,
)

# Use sys.modules to get actual module object (avoids __init__.py shadowing)
_GOLDEN_MOD = sys.modules["backend.evaluator.l3_meta.golden"]
_METAEVAL_MOD = sys.modules["backend.evaluator.l3_meta.metaeval"]


# ── Golden Set Tests ─────────────────────────────────────────────────────────

class TestGoldenSet:
    """1. Golden set: load, add, count. add_golden is human-only path."""

    def test_load_golden_empty_when_no_db(self, monkeypatch):
        monkeypatch.setattr(_GOLDEN_MOD, "_db_url", lambda: "")
        items = load_golden("build")
        assert items == []

    def test_add_golden_returns_empty_on_no_db(self, monkeypatch):
        monkeypatch.setattr(_GOLDEN_MOD, "_db_url", lambda: "")
        item_id = add_golden("test", "/tmp/artifact", "Does it work?", True)
        assert item_id == ""

    def test_golden_item_dataclass(self):
        item = GoldenItem(
            node_type="build",
            artifact_ref="/tmp/artifact.txt",
            rubric_item="Does it work?",
            human_label=True,
            expected_score=0.95,
        )
        assert item.node_type == "build"
        assert item.human_label is True
        assert item.frozen is True

    def test_golden_item_defaults(self):
        item = GoldenItem(
            node_type="test",
            artifact_ref="/tmp/a",
            rubric_item="Is it tested?",
            human_label=False,
        )
        assert item.expected_score is None
        assert item.frozen is True
        assert item.item_id == ""

    def test_count_golden_zero_on_no_db(self, monkeypatch):
        monkeypatch.setattr(_GOLDEN_MOD, "_db_url", lambda: "")
        assert count_golden() == 0


# ── Jury Tests ───────────────────────────────────────────────────────────────

class TestJury:
    """2. Jury diversity: multi-model scoring with fallback."""

    def test_jury_with_mock_models(self, monkeypatch):
        """Jury aggregates multiple model scores."""
        call_log = []

        def _mock_call(model, prompt):
            call_log.append(model)
            return {"criteria_met": True, "explanation": "ok"}

        monkeypatch.setattr(
            "backend.evaluator.l3_meta.jury._call_model",
            _mock_call,
        )

        result = jury_score(
            artifact="some code",
            rubric_item="Is it good?",
            models=["model-a", "model-b"],
        )
        assert result["criteria_met"] is True
        assert len(call_log) == 2

    def test_jury_majority_vote(self, monkeypatch):
        """Majority vote: 2 pass, 1 fail → pass."""
        votes = iter([{"criteria_met": True, "explanation": "ok"},
                       {"criteria_met": True, "explanation": "ok"},
                       {"criteria_met": False, "explanation": "bad"}])

        def _mock_call(model, prompt):
            return next(votes)

        monkeypatch.setattr(
            "backend.evaluator.l3_meta.jury._call_model",
            _mock_call,
        )

        result = jury_score(
            artifact="code",
            rubric_item="Is it good?",
            models=["a", "b", "c"],
        )
        assert result["criteria_met"] is True

    def test_jury_minority_pass(self, monkeypatch):
        """Majority fail: 1 pass, 2 fail → fail."""
        votes = iter([{"criteria_met": True, "explanation": "ok"},
                       {"criteria_met": False, "explanation": "bad"},
                       {"criteria_met": False, "explanation": "bad"}])

        def _mock_call(model, prompt):
            return next(votes)

        monkeypatch.setattr(
            "backend.evaluator.l3_meta.jury._call_model",
            _mock_call,
        )

        result = jury_score(
            artifact="code",
            rubric_item="Is it good?",
            models=["a", "b", "c"],
        )
        assert result["criteria_met"] is False

    def test_jury_all_unavailable(self, monkeypatch):
        """All models unavailable → criteria_met is None."""
        def _mock_call(model, prompt):
            return {"criteria_met": None, "explanation": "unavailable"}

        monkeypatch.setattr(
            "backend.evaluator.l3_meta.jury._call_model",
            _mock_call,
        )

        result = jury_score(
            artifact="code",
            rubric_item="Is it good?",
            models=["a"],
        )
        assert result["criteria_met"] is None
        assert "unavailable" in result["note"]

    def test_jury_single_model_family_note(self, monkeypatch):
        """Single model produces a caveat note."""
        call_log = []

        def _mock_call(model, prompt):
            call_log.append(model)
            return {"criteria_met": True, "explanation": "ok"}

        monkeypatch.setattr(
            "backend.evaluator.l3_meta.jury._call_model",
            _mock_call,
        )

        result = jury_score(
            artifact="code",
            rubric_item="Is it good?",
            models=["only-model"],
        )
        assert "Single-family" in result["note"]

    def test_jury_default_models_from_env(self, monkeypatch):
        """When no models arg, reads from JURY_MODELS env."""
        call_log = []

        def _mock_call(model, prompt):
            call_log.append(model)
            return {"criteria_met": True, "explanation": "ok"}

        monkeypatch.setattr(
            "backend.evaluator.l3_meta.jury._call_model",
            _mock_call,
        )
        monkeypatch.setattr(
            "backend.evaluator.l3_meta.jury.JURY_MODELS_RAW",
            '["env-a", "env-b"]',
        )

        result = jury_score(artifact="code", rubric_item="Is it good?")
        assert len(call_log) == 2


# ── Drift Measurement Tests ──────────────────────────────────────────────────

class TestDriftMeasurement:
    """3. Drift measurement: L2 vs human golden label comparison."""

    def test_no_disagreements(self):
        report = [
            {"l2_met": True, "human_label": True, "jury_met": True},
            {"l2_met": False, "human_label": False, "jury_met": False},
        ]
        metrics = measure_disagreement(report)
        assert metrics["disagreements"] == 0
        assert metrics["disagreement_rate"] == 0.0

    def test_some_disagreements(self):
        report = [
            {"l2_met": True, "human_label": True, "jury_met": True},
            {"l2_met": True, "human_label": False, "jury_met": False},
            {"l2_met": False, "human_label": False, "jury_met": True},
        ]
        metrics = measure_disagreement(report)
        assert metrics["disagreements"] == 1
        assert metrics["disagreement_rate"] == pytest.approx(0.3333, abs=0.0001)

    def test_jury_supports_human_when_l2_disagrees(self):
        report = [
            {"l2_met": True, "human_label": False, "jury_met": False},
            {"l2_met": True, "human_label": True, "jury_met": True},
        ]
        metrics = measure_disagreement(report)
        assert metrics["disagreements"] == 1
        assert metrics["jury_supported"] == 1

    def test_empty_report(self):
        metrics = measure_disagreement([])
        assert metrics["total"] == 0
        assert metrics["disagreement_rate"] == 0.0

    def test_jury_none_not_counted_as_support(self):
        """If jury is None (unavailable), it can't support."""
        report = [
            {"l2_met": True, "human_label": False, "jury_met": None},
        ]
        metrics = measure_disagreement(report)
        assert metrics["jury_supported"] == 0


# ── Rubric Refinement Proposal Tests ─────────────────────────────────────────

class TestRubricRefinement:
    """4. Drift → gated proposal (queued, not applied)."""

    def test_no_proposal_when_no_disagreements(self):
        report = [{"l2_met": True, "human_label": True, "jury_met": True}]
        metrics = {"disagreements": 0, "total": 1, "disagreement_rate": 0.0, "jury_supported": 0}
        proposal = propose_rubric_refinement("build", report, metrics)
        assert proposal is None

    def test_proposal_generated_on_disagreement(self):
        report = [
            {"rubric_item": "Is the code good?", "l2_met": True,
             "human_label": False, "jury_met": False},
        ]
        metrics = {"disagreements": 1, "total": 1, "disagreement_rate": 1.0, "jury_supported": 1}
        proposal = propose_rubric_refinement("build", report, metrics)
        assert proposal is not None
        assert proposal["node_type"] == "build"
        assert "disagreement_rate" in proposal["rationale"]

    def test_proposal_contains_old_and_new_rubric(self):
        report = [
            {"rubric_item": "Is the code correct?",
             "l2_met": True, "human_label": False, "jury_met": False},
        ]
        metrics = {"disagreements": 1, "total": 1, "disagreement_rate": 1.0, "jury_supported": 0}
        proposal = propose_rubric_refinement("test", report, metrics)
        assert proposal is not None
        assert proposal["old_rubric"] == "Is the code correct?"

    def test_queue_returns_empty_on_no_db(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        proposal_id = queue_for_approval({"node_type": "build", "rationale": "test"})
        assert proposal_id == ""


# ── Anchor Integrity Tests ────────────────────────────────────────────────────

class TestAnchorIntegrity:
    """5. Nothing auto-writes the golden set; judge never scores its own output."""

    def test_no_auto_writer_in_pipeline(self):
        """Verify load_golden and count_golden never write."""
        from backend.evaluator.l3_meta import golden
        import inspect
        source = inspect.getsource(golden)
        # Ensure no INSERT or UPDATE in load/count functions
        # (add_golden is the ONLY write function)
        write_keywords = ["INSERT INTO golden_set"]
        add_count = source.count("INSERT INTO golden_set")
        # Only add_golden should contain INSERT INTO golden_set
        assert add_count == 1, (
            f"Found {add_count} INSERT INTO golden_set — "
            f"only add_golden may write to golden_set"
        )
