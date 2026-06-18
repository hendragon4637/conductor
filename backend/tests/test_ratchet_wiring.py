"""Tests for File 04: Ratchet wiring — scores, mining, validation, scope gating.

[GATE 04]
"""
from __future__ import annotations

import json
import sys

import pytest

from backend.ratchet.scope import detect_scope
from backend.ratchet.mutate import _reject_frozen_target
from backend.ratchet.failures import mine_failures
from backend.ratchet.decide import decide
from backend.ratchet.validate import validate

# __init__.py imports shadow module names with their function exports.
# Use sys.modules to get the actual module object for monkeypatching.
_DECIDE_MOD = sys.modules["backend.ratchet.decide"]
_VALIDATE_MOD = sys.modules["backend.ratchet.validate"]


# ── Scope Detection Tests ────────────────────────────────────────────────────

class TestDetectScope:
    """1. Scope detection from agent_config_id."""

    def test_backend_config_is_global(self, monkeypatch):
        monkeypatch.setattr("backend.ratchet.scope._resolve_domain", lambda _: "backend")
        assert detect_scope("opencode:backend-executor") == "global"

    def test_general_config_is_global(self, monkeypatch):
        monkeypatch.setattr("backend.ratchet.scope._resolve_domain", lambda _: "general")
        assert detect_scope("orchestrator") == "global"

    def test_project_config_is_project(self, monkeypatch):
        monkeypatch.setattr("backend.ratchet.scope._resolve_domain", lambda _: "badminton")
        assert detect_scope("badminton-executor") == "project"

    def test_finance_config_is_project(self, monkeypatch):
        monkeypatch.setattr("backend.ratchet.scope._resolve_domain", lambda _: "finance")
        assert detect_scope("finance-planner") == "project"


# ── Frozen-Boundary Enforcement Tests ────────────────────────────────────────

class TestRejectFrozenTarget:
    """2. Frozen-boundary enforcement in propose_mutation."""

    def test_skill_target_is_allowed(self):
        _reject_frozen_target("skill", "# Updated skill content")

    def test_agents_md_target_is_allowed(self):
        _reject_frozen_target("agents_md", "## Agent config")

    def test_prompt_target_is_allowed(self):
        _reject_frozen_target("prompt", "You are a helpful assistant")

    def test_permission_template_is_rejected(self):
        with pytest.raises(ValueError, match="frozen artifact"):
            _reject_frozen_target("permission_template", "{}")

    def test_engine_is_rejected(self):
        with pytest.raises(ValueError, match="frozen artifact"):
            _reject_frozen_target("engine", "opencode")

    def test_model_is_rejected(self):
        with pytest.raises(ValueError, match="frozen artifact"):
            _reject_frozen_target("model", "gpt-4")

    def test_golden_set_is_rejected(self):
        with pytest.raises(ValueError, match="frozen artifact"):
            _reject_frozen_target("golden", "")

    def test_candidate_content_with_permission_keyword_is_rejected(self):
        with pytest.raises(ValueError, match="frozen keyword"):
            _reject_frozen_target("skill", "permission: allow all")

    def test_innocent_content_with_permission_word_passes(self):
        _reject_frozen_target("skill", "Handle permission errors gracefully")


# ── Mine Failures Tests ──────────────────────────────────────────────────────

class TestMineFailures:
    """3. mine_failures clusters recurring rubric failures from score data."""

    def _make_score_entry(self, trace_id, value, comment):
        return {"traceId": trace_id, "value": value, "comment": comment}

    def _make_trace(self, trace_id, agent_config="backend-executor"):
        return {
            "metadata": {"agent_config": agent_config},
            "input": {},
            "output": {},
        }

    def test_returns_recurring_patterns(self, monkeypatch):
        """Mine returns patterns with count >= min_count."""
        # 3 traces with the same rubric failure
        scores = [
            self._make_score_entry("t1", 0.4, "rubric-edge-cases: FAIL (missing) | rubric-quality: pass (ok)"),
            self._make_score_entry("t2", 0.5, "rubric-edge-cases: FAIL (missing) | rubric-quality: pass (ok)"),
            self._make_score_entry("t3", 0.3, "rubric-edge-cases: FAIL (missing)"),
        ]
        monkeypatch.setattr("backend.ratchet.failures._get_scores", lambda name, limit: scores)
        monkeypatch.setattr("backend.ratchet.failures._get_trace",
                            lambda tid: self._make_trace(tid))

        patterns = mine_failures("backend-executor", min_count=2)
        assert len(patterns) >= 1
        edge_pattern = [p for p in patterns if p["check_id"] == "rubric-edge-cases"]
        assert len(edge_pattern) == 1
        assert edge_pattern[0]["count"] >= 2

    def test_skips_one_offs(self, monkeypatch):
        """Single occurrence below min_count is excluded."""
        scores = [
            self._make_score_entry("t1", 0.4, "rubric-edge-cases: FAIL (missing)"),
            self._make_score_entry("t2", 0.9, "rubric-quality: pass (ok)"),
        ]
        monkeypatch.setattr("backend.ratchet.failures._get_scores", lambda name, limit: scores)
        monkeypatch.setattr("backend.ratchet.failures._get_trace",
                            lambda tid: self._make_trace(tid))

        patterns = mine_failures("backend-executor", min_count=2)
        assert len(patterns) == 0

    def test_filters_by_agent_config(self, monkeypatch):
        """Only traces matching agent_config_id are considered."""
        scores = [
            self._make_score_entry("t1", 0.4, "rubric-edge-cases: FAIL (missing)"),
            self._make_score_entry("t2", 0.3, "rubric-edge-cases: FAIL (still missing)"),
        ]
        monkeypatch.setattr("backend.ratchet.failures._get_scores", lambda name, limit: scores)

        def _trace_or_default(tid):
            if tid == "t1":
                return self._make_trace(tid, agent_config="backend-executor")
            return self._make_trace(tid, agent_config="other-config")

        monkeypatch.setattr("backend.ratchet.failures._get_trace", _trace_or_default)

        patterns = mine_failures("backend-executor", min_count=1)
        assert len(patterns) == 1

    def test_skips_high_scoring_traces(self, monkeypatch):
        """Traces with score >= threshold are not mined."""
        scores = [
            self._make_score_entry("t1", 0.8, "rubric-edge-cases: FAIL (missing)"),
            self._make_score_entry("t2", 0.9, "rubric-edge-cases: FAIL (missing)"),
        ]
        monkeypatch.setattr("backend.ratchet.failures._get_scores", lambda name, limit: scores)
        monkeypatch.setattr("backend.ratchet.failures._get_trace",
                            lambda tid: self._make_trace(tid))

        patterns = mine_failures("backend-executor", min_count=1, score_threshold=0.7)
        # Both traces are above threshold, so no patterns mined
        assert len(patterns) == 0

    def test_handles_empty_scores(self, monkeypatch):
        monkeypatch.setattr("backend.ratchet.failures._get_scores", lambda name, limit: [])
        patterns = mine_failures("backend-executor")
        assert patterns == []


# ── Decide Scope Gating Tests ────────────────────────────────────────────────

class TestDecideScopeGating:
    """4. Scope gating in decide: global → queued, project → kept."""

    @pytest.fixture
    def mock_experiment(self):
        return {
            "experiment_id": "exp-test-001",
            "agent_config_id": "opencode:backend-executor",
            "baseline_score": "0.50",
            "candidate_score": "0.80",
            "target": "skill",
        }

    def _patch_everything(self, monkeypatch, mock_exp, scope="global"):
        monkeypatch.setattr(_DECIDE_MOD, "load_experiment", lambda eid: mock_exp)
        monkeypatch.setattr(_DECIDE_MOD, "_get_db", lambda: "postgresql://test:test@localhost/test")
        monkeypatch.setattr(_DECIDE_MOD, "_detect_scope", lambda cfg: scope)
        # No-op the side-effect functions
        monkeypatch.setattr(_DECIDE_MOD, "apply_mutation", lambda exp: None)
        monkeypatch.setattr(_DECIDE_MOD, "git_tag", lambda exp: None)
        monkeypatch.setattr(_DECIDE_MOD, "record_mutation", lambda *a, **kw: None)

        # Mock psycopg connect
        class FakeCursor:
            def execute(self, sql, params): pass
            def __enter__(self): return self
            def __exit__(self, *a): pass

        class FakeConn:
            def cursor(self): return FakeCursor()
            def commit(self): pass
            def __enter__(self): return self
            def __exit__(self, *a): pass

        monkeypatch.setattr("psycopg.connect", lambda url, **kw: FakeConn())

    def test_global_winner_queued(self, monkeypatch, mock_experiment):
        """Global scope + delta >= threshold → queued."""
        self._patch_everything(monkeypatch, mock_experiment, scope="global")
        result = decide("exp-test-001", delta_threshold=0.03)
        assert result == "queued"

    def test_project_winner_kept(self, monkeypatch, mock_experiment):
        """Project scope + delta >= threshold → kept."""
        self._patch_everything(monkeypatch, mock_experiment, scope="project")
        result = decide("exp-test-001", delta_threshold=0.03)
        assert result == "kept"

    def test_low_delta_reverts_regardless_of_scope(self, monkeypatch, mock_experiment):
        """Delta below threshold → reverted even for global."""
        mock_experiment["baseline_score"] = "0.75"
        mock_experiment["candidate_score"] = "0.76"
        self._patch_everything(monkeypatch, mock_experiment, scope="global")
        result = decide("exp-test-001", delta_threshold=0.03)
        assert result == "reverted"

    def test_global_winner_does_not_apply_mutation(self, monkeypatch, mock_experiment):
        """Global winner → apply_mutation is NOT called."""
        applied = []
        monkeypatch.setattr(_DECIDE_MOD, "apply_mutation",
                            lambda exp: applied.append(True))
        self._patch_everything(monkeypatch, mock_experiment, scope="global")
        decide("exp-test-001", delta_threshold=0.03)
        assert len(applied) == 0


# ── Validate Held-Out Tests ──────────────────────────────────────────────────

class TestValidateHeldOut:
    """5. Held-out regression check in validate."""

    def test_no_held_out_keeps(self, monkeypatch):
        """Without held_out list, decision follows delta threshold."""
        monkeypatch.setattr(
            _VALIDATE_MOD, "candidate_score",
            lambda ac, mu: {
                "experiment_id": "exp-test",
                "baseline_score": 0.50,
                "candidate_score": 0.80,
                "delta": 0.30,
                "task_results": [],
            },
        )
        result = validate("backend-executor", {"target": "skill", "candidate": "# new skill"})
        assert result["decision"] == "keep"

    def test_held_out_regression_reverts(self, monkeypatch):
        """Candidate regresses on a held-out task → revert."""
        monkeypatch.setattr(
            _VALIDATE_MOD, "candidate_score",
            lambda ac, mu: {
                "experiment_id": "exp-test",
                "baseline_score": 0.60,
                "candidate_score": 0.65,
                "delta": 0.05,
                "task_results": [
                    {"task": "t1.md", "baseline_score": 0.9, "candidate_score": 0.9},
                    {"task": "t2.md", "baseline_score": 0.8, "candidate_score": 0.3},
                ],
            },
        )
        result = validate(
            "backend-executor",
            {"target": "skill", "candidate": "# new skill"},
            held_out=["t2.md"],
            delta_threshold=0.03,
        )
        assert result["decision"] == "revert"
        assert result["held_out_regressed"] is True

    def test_held_out_no_regression_keeps(self, monkeypatch):
        """Candidate does not regress on held-out → keep."""
        monkeypatch.setattr(
            _VALIDATE_MOD, "candidate_score",
            lambda ac, mu: {
                "experiment_id": "exp-test",
                "baseline_score": 0.50,
                "candidate_score": 0.80,
                "delta": 0.30,
                "task_results": [
                    {"task": "t1.md", "baseline_score": 0.5, "candidate_score": 0.8},
                    {"task": "t2.md", "baseline_score": 0.7, "candidate_score": 0.75},
                ],
            },
        )
        result = validate(
            "backend-executor",
            {"target": "skill", "candidate": "# new skill"},
            held_out=["t2.md"],
        )
        assert result["decision"] == "keep"
        assert result["held_out_regressed"] is False

    def test_low_delta_reverts_even_without_held_out(self, monkeypatch):
        """Delta below threshold → revert."""
        monkeypatch.setattr(
            _VALIDATE_MOD, "candidate_score",
            lambda ac, mu: {
                "experiment_id": "exp-test",
                "baseline_score": 0.70,
                "candidate_score": 0.71,
                "delta": 0.01,
                "task_results": [],
            },
        )
        result = validate(
            "backend-executor",
            {"target": "skill", "candidate": "# new skill"},
            delta_threshold=0.03,
        )
        assert result["decision"] == "revert"
