"""Tests for judge_ratchet: mine_disagreements, propose_rubric_mutation,
rescore_split, run_judge_ratchet, and utility functions.

All tests use monkeypatching to avoid real DB or LLM calls.
"""
from __future__ import annotations

import json

import pytest

from backend.evaluator.judge_ratchet import (
    DimDisagreement,
    MinedDisagreements,
    _better,
    mine_disagreements,
    propose_rubric_mutation,
    rescore_split,
    rollback_rubric,
    run_judge_ratchet,
)
from backend.evaluator.l2_judge import L2Result


# ── Sample test data ────────────────────────────────────────────────────────

_GOLDEN_ITEM = {
    "id": "dim1",
    "rubric_item": "Code quality and correctness",
    "expected_score": 0.8,
    "human_label": True,
    "artifact_blob": "def foo(): pass\n",
    "split": "calibration",
}

_GOLDEN_ITEM_LENIENT = {
    "id": "dim2",
    "rubric_item": "Documentation clarity",
    "expected_score": 0.3,
    "human_label": False,
    "artifact_blob": "print('hello')\n",
    "split": "calibration",
}

_RUBRIC_CFG = {
    "anchors": [
        {"score_range": [0, 2], "expected_outcome": "poor"},
        {"score_range": [3, 5], "expected_outcome": "fair"},
    ],
    "feedback_contract": "Output JSON only",
    "bundles": {},
    "dimensions": [
        {"id": "cal-dim1", "rubric_item": "Code quality?", "evaluation_steps": ["Check correctness"], "weight": 1.0},
    ],
}


def _l2(score: float = 0.8) -> L2Result:
    r = L2Result()
    r.score = score
    r.judgments = []
    return r


def _mined() -> MinedDisagreements:
    return MinedDisagreements(
        capability="executor",
        dims=[DimDisagreement(dim_id="cal-dim1", rubric_item="Q?", direction="harsh", count=1)],
        total_items=1,
    )


def _mutation() -> dict:
    return {"target": "step", "dim": "cal-dim1", "diff": "Check correctness", "rationale": "Fix harsh scoring"}


# ── TestMineDisagreements ───────────────────────────────────────────────────

class TestMineDisagreements:
    """mine_disagreements: compare judge scores vs golden labels."""

    def test_no_disagreements_returns_none(self, monkeypatch):
        """abs_err ≤ 0.15 → return None."""
        monkeypatch.setattr(
            "backend.evaluator.judge_ratchet._load_golden_items",
            lambda *a, **kw: [_GOLDEN_ITEM],
        )
        monkeypatch.setattr(
            "backend.evaluator.judge_ratchet.load_rubric_config",
            lambda *a: _RUBRIC_CFG,
        )
        monkeypatch.setattr(
            "backend.evaluator.judge_ratchet.run_l2",
            lambda **kw: _l2(0.75),  # |0.75 - 0.8| = 0.05 ≤ 0.15
        )
        assert mine_disagreements("executor") is None

    def test_disagreements_detected(self, monkeypatch):
        """abs_err > 0.15 → MinedDisagreements with correct dim/direction."""
        monkeypatch.setattr(
            "backend.evaluator.judge_ratchet._load_golden_items",
            lambda *a, **kw: [_GOLDEN_ITEM],
        )
        monkeypatch.setattr(
            "backend.evaluator.judge_ratchet.load_rubric_config",
            lambda *a: _RUBRIC_CFG,
        )
        monkeypatch.setattr(
            "backend.evaluator.judge_ratchet.run_l2",
            lambda **kw: _l2(0.3),  # |0.3 - 0.8| = 0.5 > 0.15
        )
        mined = mine_disagreements("executor")
        assert mined is not None
        assert len(mined.dims) == 1
        assert mined.dims[0].direction == "harsh"
        assert mined.total_items == 1

    def test_no_golden_items_returns_none(self, monkeypatch):
        """Empty golden list → None."""
        monkeypatch.setattr(
            "backend.evaluator.judge_ratchet._load_golden_items",
            lambda *a, **kw: [],
        )
        assert mine_disagreements("executor") is None

    def test_lenient_direction_detected(self, monkeypatch):
        """Judge scoring higher than human → direction='lenient'."""
        monkeypatch.setattr(
            "backend.evaluator.judge_ratchet._load_golden_items",
            lambda *a, **kw: [_GOLDEN_ITEM_LENIENT],
        )
        monkeypatch.setattr(
            "backend.evaluator.judge_ratchet.load_rubric_config",
            lambda *a: _RUBRIC_CFG,
        )
        monkeypatch.setattr(
            "backend.evaluator.judge_ratchet.run_l2",
            lambda **kw: _l2(0.8),  # |0.8 - 0.3| = 0.5 > 0.15, judge > human
        )
        mined = mine_disagreements("executor")
        assert mined is not None
        assert mined.dims[0].direction == "lenient"


# ── TestProposeMutation ─────────────────────────────────────────────────────

class TestProposeMutation:
    """propose_rubric_mutation: LLM-proposed rubric edits."""

    def test_no_dims_returns_none(self):
        """Empty dims → None."""
        mined = MinedDisagreements(capability="executor", dims=[], total_items=0)
        assert propose_rubric_mutation(mined) is None

    def test_boundary_rejected_forbidden_phrase(self, monkeypatch):
        """Forbidden phrase in diff → _rejected='boundary_violation'."""
        monkeypatch.setattr(
            "backend.evaluator.judge_ratchet.load_rubric_config",
            lambda *a: _RUBRIC_CFG,
        )
        monkeypatch.setattr(
            "backend.llm.gateway.call",
            lambda *a, **kw: {
                "choices": [{
                    "message": {
                        "content": json.dumps({
                            "target": "step",
                            "dim": "cal-dim1",
                            "diff": "Check for agreement with expectations",
                            "rationale": "test",
                        }),
                    }
                }]
            },
        )
        result = propose_rubric_mutation(_mined())
        assert result is not None
        assert result.get("_rejected") == "boundary_violation"

    def test_missing_keys_rejected(self, monkeypatch):
        """Incomplete mutation dict → None."""
        monkeypatch.setattr(
            "backend.evaluator.judge_ratchet.load_rubric_config",
            lambda *a: _RUBRIC_CFG,
        )
        monkeypatch.setattr(
            "backend.llm.gateway.call",
            lambda *a, **kw: {
                "choices": [{
                    "message": {
                        "content": json.dumps({"target": "step", "dim": "cal-dim1"}),
                    }
                }]
            },
        )
        assert propose_rubric_mutation(_mined()) is None

    def test_invalid_target_rejected(self, monkeypatch):
        """Invalid mutation target → None."""
        monkeypatch.setattr(
            "backend.evaluator.judge_ratchet.load_rubric_config",
            lambda *a: _RUBRIC_CFG,
        )
        monkeypatch.setattr(
            "backend.llm.gateway.call",
            lambda *a, **kw: {
                "choices": [{
                    "message": {
                        "content": json.dumps({
                            "target": "invalid_thing",
                            "dim": "cal-dim1",
                            "diff": "Some change",
                            "rationale": "test",
                        }),
                    }
                }]
            },
        )
        assert propose_rubric_mutation(_mined()) is None


# ── TestRescoreSplit ────────────────────────────────────────────────────────

class TestRescoreSplit:
    """rescore_split: re-score frozen golden artifacts."""

    def test_empty_split_returns_defaults(self, monkeypatch):
        """No golden items → items=0, mae=0.0, agreement=0.0."""
        monkeypatch.setattr(
            "backend.evaluator.judge_ratchet._load_golden_items",
            lambda *a, **kw: [],
        )
        result = rescore_split("executor", "calibration", _RUBRIC_CFG)
        assert result["items"] == 0
        assert result["mae"] == 0.0
        assert result["agreement"] == 0.0

    def test_scoring_logic(self, monkeypatch):
        """Single item: correct mae and agreement when judge matches human."""
        monkeypatch.setattr(
            "backend.evaluator.judge_ratchet._load_golden_items",
            lambda *a, **kw: [_GOLDEN_ITEM],
        )
        monkeypatch.setattr(
            "backend.evaluator.judge_ratchet.run_l2",
            lambda **kw: _l2(0.8),  # matches human_score exactly
        )
        result = rescore_split("executor", "calibration", _RUBRIC_CFG)
        assert result["items"] == 1
        assert result["mae"] == 0.0       # |0.8 - 0.8| = 0.0
        assert result["agreement"] == 1.0  # 0.0 ≤ 0.15 → agree

    def test_scoring_disagreement(self, monkeypatch):
        """Judge score far from human → mae > 0, agreement 0."""
        monkeypatch.setattr(
            "backend.evaluator.judge_ratchet._load_golden_items",
            lambda *a, **kw: [_GOLDEN_ITEM],
        )
        monkeypatch.setattr(
            "backend.evaluator.judge_ratchet.run_l2",
            lambda **kw: _l2(0.3),  # |0.3 - 0.8| = 0.5
        )
        result = rescore_split("executor", "calibration", _RUBRIC_CFG)
        assert result["items"] == 1
        assert result["mae"] == 0.5       # mean(abs_err) = 0.5
        assert result["agreement"] == 0.0  # 0.5 > 0.15 → not agree


# ── TestRunJudgeRatchet ─────────────────────────────────────────────────────

class TestRunJudgeRatchet:
    """run_judge_ratchet: full multi-cycle loop."""

    def test_no_active_rubric_returns_empty(self, monkeypatch):
        """No active rubric ID → []."""
        monkeypatch.setattr(
            "backend.evaluator.judge_ratchet._resolve_active_rubric_id",
            lambda *a: "",
        )
        assert run_judge_ratchet("executor") == []

    def test_dry_run_does_not_persist(self, monkeypatch):
        """dry_run=True → returns without DB hits."""
        monkeypatch.setattr(
            "backend.evaluator.judge_ratchet._resolve_active_rubric_id",
            lambda *a: "rubric-1",
        )
        monkeypatch.setattr(
            "backend.evaluator.judge_ratchet.mine_disagreements",
            lambda *a: _mined(),
        )
        monkeypatch.setattr(
            "backend.evaluator.judge_ratchet.propose_rubric_mutation",
            lambda *a, **kw: _mutation(),
        )
        results = run_judge_ratchet("executor", dry_run=True)
        assert len(results) >= 1
        assert results[0].decision == "kept"
        assert "Dry run" in results[0].rationale

    def test_nothing_to_mine_breaks(self, monkeypatch):
        """mine_disagreements returns None → nothing_to_mine decision."""
        monkeypatch.setattr(
            "backend.evaluator.judge_ratchet._resolve_active_rubric_id",
            lambda *a: "rubric-1",
        )
        monkeypatch.setattr(
            "backend.evaluator.judge_ratchet.mine_disagreements",
            lambda *a: None,
        )
        results = run_judge_ratchet("executor")
        assert len(results) == 1
        assert results[0].decision == "nothing_to_mine"

    def test_keep_revert_decision(self, monkeypatch):
        """Both splits improve → kept; otherwise → reverted.

        Uses mocked _better to control decision outcome.
        """
        monkeypatch.setattr(
            "backend.evaluator.judge_ratchet._resolve_active_rubric_id",
            lambda *a: "rubric-1",
        )
        monkeypatch.setattr(
            "backend.evaluator.judge_ratchet.mine_disagreements",
            lambda *a: _mined(),
        )
        monkeypatch.setattr(
            "backend.evaluator.judge_ratchet.propose_rubric_mutation",
            lambda *a, **kw: _mutation(),
        )
        monkeypatch.setattr(
            "backend.evaluator.judge_ratchet.load_rubric_config",
            lambda *a: _RUBRIC_CFG,
        )

        # Provide a uniform rescore result — _better's mock controls the decision
        _RESCORE = {"agreement": 0.8, "mae": 0.1, "items": 1, "mean": 0.8, "scores": [0.8]}
        monkeypatch.setattr(
            "backend.evaluator.judge_ratchet.rescore_split",
            lambda *a, **kw: _RESCORE,
        )
        monkeypatch.setattr(
            "backend.evaluator.judge_ratchet._activate_candidate_rubric",
            lambda *a, **kw: None,
        )
        monkeypatch.setattr(
            "backend.evaluator.judge_ratchet.calibrate",
            lambda *a: None,
        )
        monkeypatch.setattr(
            "backend.evaluator.judge_ratchet._record_judge_experiment",
            lambda *a, **kw: None,
        )

        # Both improve → kept
        monkeypatch.setattr(
            "backend.evaluator.judge_ratchet._better",
            lambda c, ctrl: True,
        )
        results = run_judge_ratchet("executor")
        assert len(results) >= 1
        assert results[0].decision == "kept"

        # Neither improves → reverted
        monkeypatch.setattr(
            "backend.evaluator.judge_ratchet._better",
            lambda c, ctrl: False,
        )
        results = run_judge_ratchet("executor")
        assert len(results) >= 1
        assert results[0].decision == "reverted"

    # ── Lock-related tests ─────────────────────────────────────────────────

    def test_lock_acquired_in_non_dry_run(self, monkeypatch):
        """Lock is acquired and released in non-dry-run mode."""
        lock_calls: list[tuple] = []
        def mock_acquire(cap, which):
            lock_calls.append(("acquire", cap, which))
            return True
        def mock_release(cap, which):
            lock_calls.append(("release", cap, which))

        monkeypatch.setattr(
            "backend.evaluator.judge_ratchet.acquire_ratchet_lock", mock_acquire,
        )
        monkeypatch.setattr(
            "backend.evaluator.judge_ratchet.release_ratchet_lock", mock_release,
        )
        monkeypatch.setattr(
            "backend.evaluator.judge_ratchet.assert_no_ratchet_lock",
            lambda cap, which: None,
        )

        # Mock all the DB-dependent calls
        monkeypatch.setattr(
            "backend.evaluator.judge_ratchet._resolve_active_rubric_id",
            lambda _: "rubric-1",
        )
        monkeypatch.setattr(
            "backend.evaluator.judge_ratchet.mine_disagreements",
            lambda _: None,  # Nothing to mine → early break
        )

        run_judge_ratchet("executor", dry_run=False)

        assert len(lock_calls) > 0
        assert lock_calls[0] == ("acquire", "executor", "judge")

    def test_lock_not_acquired_in_dry_run(self, monkeypatch):
        """Lock is NOT acquired in dry-run mode."""
        lock_calls: list[tuple] = []
        def mock_acquire(cap, which):
            lock_calls.append(("acquire", cap, which))
            return True
        def mock_release(cap, which):
            lock_calls.append(("release", cap, which))

        monkeypatch.setattr(
            "backend.evaluator.judge_ratchet.acquire_ratchet_lock", mock_acquire,
        )
        monkeypatch.setattr(
            "backend.evaluator.judge_ratchet.release_ratchet_lock", mock_release,
        )
        monkeypatch.setattr(
            "backend.evaluator.judge_ratchet._resolve_active_rubric_id",
            lambda _: "rubric-1",
        )

        run_judge_ratchet("executor", dry_run=True)

        assert len(lock_calls) == 0, "No lock calls expected in dry run"

    def test_lock_refused_returns_early(self, monkeypatch):
        """When lock is refused, the ratchet returns with rejected_boundary.

        This exercises the branch where acquire_ratchet_lock returns False.
        """
        monkeypatch.setattr(
            "backend.evaluator.judge_ratchet._resolve_active_rubric_id",
            lambda _: "rubric-1",
        )
        monkeypatch.setattr(
            "backend.evaluator.judge_ratchet.acquire_ratchet_lock",
            lambda cap, which: False,
        )
        monkeypatch.setattr(
            "backend.evaluator.judge_ratchet.assert_no_ratchet_lock",
            lambda cap, which: None,
        )

        results = run_judge_ratchet("executor", dry_run=False)

        assert len(results) == 1
        assert results[0].decision == "rejected_boundary"
        assert "lock refused" in results[0].rationale.lower()

    def test_lock_assert_raises_propagates(self, monkeypatch):
        """assert_no_ratchet_lock raises → RuntimeError propagates (precondition)."""
        monkeypatch.setattr(
            "backend.evaluator.judge_ratchet._resolve_active_rubric_id",
            lambda _: "rubric-1",
        )
        monkeypatch.setattr(
            "backend.evaluator.judge_ratchet.assert_no_ratchet_lock",
            lambda cap, which: (_ for _ in ()).throw(
                RuntimeError("one ruler at a time"),
            ),
        )

        with pytest.raises(RuntimeError, match="one ruler at a time"):
            run_judge_ratchet("executor", dry_run=False)

    def test_lock_released_even_on_error(self, monkeypatch):
        """Lock is released in the finally block even if the ratchet errors."""
        lock_calls: list[tuple] = []
        def mock_acquire(cap, which):
            lock_calls.append(("acquire", cap, which))
            return True
        def mock_release(cap, which):
            lock_calls.append(("release", cap, which))

        monkeypatch.setattr(
            "backend.evaluator.judge_ratchet.acquire_ratchet_lock", mock_acquire,
        )
        monkeypatch.setattr(
            "backend.evaluator.judge_ratchet.release_ratchet_lock", mock_release,
        )
        monkeypatch.setattr(
            "backend.evaluator.judge_ratchet.assert_no_ratchet_lock",
            lambda cap, which: None,
        )
        monkeypatch.setattr(
            "backend.evaluator.judge_ratchet._resolve_active_rubric_id",
            lambda _: "rubric-1",
        )
        # Cause an exception during the cycle
        monkeypatch.setattr(
            "backend.evaluator.judge_ratchet.mine_disagreements",
            lambda _: (_ for _ in ()).throw(RuntimeError("Unexpected error")),
        )

        with pytest.raises(RuntimeError, match="Unexpected error"):
            run_judge_ratchet("executor", dry_run=False)

        # Lock should have been acquired AND released
        assert len(lock_calls) == 2
        assert lock_calls[0] == ("acquire", "executor", "judge")
        assert lock_calls[1] == ("release", "executor", "judge")


# ── TestUtils ───────────────────────────────────────────────────────────────

class TestUtils:
    """Utility functions (pure logic, no mocks needed for most)."""

    def test_rollback_rubric_no_db_url(self, monkeypatch):
        """No DATABASE_URL → returns False."""
        monkeypatch.delenv("DATABASE_URL", raising=False)
        assert rollback_rubric("executor", 1) is False

    def test_better_agreement_up_mae_not_worse(self):
        """agreement up + mae not worse → True."""
        control = {"agreement": 0.7, "mae": 0.2, "items": 1}
        candidate = {"agreement": 0.8, "mae": 0.15, "items": 1}
        assert _better(candidate, control) is True

    def test_better_agreement_down(self):
        """agreement down → False."""
        control = {"agreement": 0.7, "mae": 0.2, "items": 1}
        candidate = {"agreement": 0.6, "mae": 0.15, "items": 1}
        assert _better(candidate, control) is False

    def test_better_agreement_not_enough_improvement(self):
        """agreement same (needs +0.02) → False."""
        control = {"agreement": 0.7, "mae": 0.2, "items": 1}
        candidate = {"agreement": 0.71, "mae": 0.15, "items": 1}
        assert _better(candidate, control) is False

    def test_better_candidate_no_items(self):
        """candidate items=0 → False."""
        control = {"agreement": 0.7, "mae": 0.2, "items": 1}
        candidate = {"agreement": 0.0, "mae": 0.0, "items": 0}
        assert _better(candidate, control) is False

    def test_better_control_no_items(self):
        """control items=0 → True (candidate has items)."""
        control = {"agreement": 0.0, "mae": 0.0, "items": 0}
        candidate = {"agreement": 0.8, "mae": 0.15, "items": 1}
        assert _better(candidate, control) is True

    def test_better_mae_worse_reverts(self):
        """mae worse → False even if agreement improves."""
        control = {"agreement": 0.7, "mae": 0.2, "items": 1}
        candidate = {"agreement": 0.8, "mae": 0.25, "items": 1}
        # agreement up (0.8 >= 0.7+0.02) but mae worse (0.25 > 0.2+0.01)
        assert _better(candidate, control) is False
