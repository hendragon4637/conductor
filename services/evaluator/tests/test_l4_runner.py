"""Tests for services/evaluator/l4_runner.py — core L4 orchestration logic.

Covers: _validate_report (6 outcomes), _should_publish (3 gates),
_emit_l4_findings (severity floor filter), and helpers.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch, ANY

import pytest

from shared.l4_models import L4Report, Scenario, ScenarioResult, Finding
from services.evaluator.l4_runner import (
    _validate_report,
    _should_publish,
    _emit_l4_findings,
    SEVERITY_RANK,
    MIN_SEVERITY_RANK,
)


# ── Fixtures ───────────────────────────────────────────────────────────

@pytest.fixture
def seeded_scenarios():
    return [
        Scenario(id="s1", as_a="user", wants="login", success_looks_like="dashboard"),
        Scenario(id="s2", as_a="admin", wants="configure", success_looks_like="settings saved"),
    ]


@pytest.fixture
def valid_report_dict():
    return {
        "verdict": "fail",
        "scenario_results": [
            {"scenario_id": "s1", "attempted": ["clicked login"], "outcome": "fail"},
            {"scenario_id": "s2", "attempted": ["opened settings"], "outcome": "pass"},
        ],
        "findings": [
            {
                "what": "Login button unresponsive",
                "where": ["src/login.tsx"],
                "why": "Clicked login button, no response for 5s",
                "severity": "high",
                "scenario_id": "s1",
            },
        ],
        "observations": [],
    }


# ── _validate_report ──────────────────────────────────────────────────

class TestValidateReport:
    def test_ok_outcome(self, valid_report_dict, seeded_scenarios):
        with tempfile.TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "report.json"
            report_path.write_text(json.dumps(valid_report_dict))
            # Create the where path that the report references
            (Path(tmp) / "src").mkdir()
            (Path(tmp) / "src" / "login.tsx").write_text("")
            outcome, report = _validate_report(report_path, seeded_scenarios, tmp)
            assert outcome == "ok"
            assert report is not None

    def test_missing_file(self, seeded_scenarios):
        with tempfile.TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "nonexistent.json"
            outcome, report = _validate_report(report_path, seeded_scenarios, tmp)
            assert outcome == "missing_file"
            assert report is None

    def test_parse_error(self, seeded_scenarios):
        with tempfile.TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "report.json"
            report_path.write_text("not valid json{{{")
            outcome, report = _validate_report(report_path, seeded_scenarios, tmp)
            assert outcome == "parse_error"
            assert report is None

    def test_schema_error(self, seeded_scenarios):
        with tempfile.TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "report.json"
            report_path.write_text(json.dumps({"verdict": "invalid_verdict"}))
            outcome, report = _validate_report(report_path, seeded_scenarios, tmp)
            assert outcome == "schema_error"
            assert report is None

    def test_path_error(self, valid_report_dict, seeded_scenarios):
        """where path does not exist in worktree."""
        with tempfile.TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "report.json"
            report_path.write_text(json.dumps(valid_report_dict))
            # DON'T create src/login.tsx — path should not resolve
            outcome, report = _validate_report(report_path, seeded_scenarios, tmp)
            assert outcome == "path_error"
            assert report is None

    def test_inconsistent_outcome(self, valid_report_dict, seeded_scenarios):
        """pass verdict with findings is inconsistent."""
        with tempfile.TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "report.json"
            bad = dict(valid_report_dict)
            bad["verdict"] = "pass"  # pass + findings = inconsistent
            report_path.write_text(json.dumps(bad))
            (Path(tmp) / "src").mkdir()
            (Path(tmp) / "src" / "login.tsx").write_text("")
            outcome, report = _validate_report(report_path, seeded_scenarios, tmp)
            assert outcome.startswith("inconsistent:")
            assert report is not None  # returns report even when inconsistent


# ── _should_publish ───────────────────────────────────────────────────

class TestShouldPublish:
    def test_verdict_pass_does_not_publish(self):
        report = L4Report(
            verdict="pass",
            scenario_results=[],
            findings=[
                Finding(
                    what="Minor issue",
                    where=["src/x.ts"],
                    why="Observed minor problem",
                    severity="low",
                    scenario_id="s1",
                ),
            ],
        )
        assert _should_publish(report) is False

    def test_no_findings_does_not_publish(self):
        report = L4Report(
            verdict="fail",
            scenario_results=[
                ScenarioResult(scenario_id="s1", attempted=["x"], outcome="fail"),
            ],
            findings=[],
        )
        assert _should_publish(report) is False

    def test_verdict_fail_with_high_publishes(self):
        report = L4Report(
            verdict="fail",
            scenario_results=[
                ScenarioResult(scenario_id="s1", attempted=["x"], outcome="fail"),
            ],
            findings=[
                Finding(
                    what="Critical issue",
                    where=["src/x.ts"],
                    why="Observed critical problem",
                    severity="high",
                    scenario_id="s1",
                ),
            ],
        )
        assert _should_publish(report) is True

    def test_verdict_partial_with_medium_publishes(self):
        report = L4Report(
            verdict="partial",
            scenario_results=[
                ScenarioResult(scenario_id="s1", attempted=["x"], outcome="fail"),
            ],
            findings=[
                Finding(
                    what="Medium issue",
                    where=["src/x.ts"],
                    why="Observed medium problem",
                    severity="medium",
                    scenario_id="s1",
                ),
            ],
        )
        assert _should_publish(report) is True

    def test_verdict_partial_with_low_does_not_publish_below_floor(self):
        """Default floor is medium (rank >= 1). Low findings don't publish."""
        report = L4Report(
            verdict="partial",
            scenario_results=[
                ScenarioResult(scenario_id="s1", attempted=["x"], outcome="fail"),
            ],
            findings=[
                Finding(
                    what="Low severity issue here",
                    where=["src/x.ts"],
                    why="Observed low problem detected",
                    severity="low",
                    scenario_id="s1",
                ),
            ],
        )
        assert _should_publish(report) is False

    def test_verdict_fail_with_low_does_not_publish_below_floor(self):
        report = L4Report(
            verdict="fail",
            scenario_results=[
                ScenarioResult(scenario_id="s1", attempted=["x"], outcome="fail"),
            ],
            findings=[
                Finding(
                    what="Low severity issue here",
                    where=["src/x.ts"],
                    why="Observed low problem",
                    severity="low",
                    scenario_id="s1",
                ),
            ],
        )
        assert _should_publish(report) is False

    def test_mixed_severities_publishes_if_any_at_or_above_floor(self):
        """Medium finding in a mix triggers publish."""
        report = L4Report(
            verdict="fail",
            scenario_results=[
                ScenarioResult(scenario_id="s1", attempted=["x"], outcome="fail"),
            ],
            findings=[
                Finding(
                    what="Low severity issue",
                    where=["src/low.ts"],
                    why="Observed low problem here",
                    severity="low",
                    scenario_id="s1",
                ),
                Finding(
                    what="Medium severity issue",
                    where=["src/med.ts"],
                    why="Observed medium problem here",
                    severity="medium",
                    scenario_id="s1",
                ),
            ],
        )
        assert _should_publish(report) is True


# ── _emit_l4_findings ────────────────────────────────────────────────

class TestEmitL4Findings:
    def test_emit_called_only_for_qualifying_findings(self):
        """Findings below severity floor are excluded."""
        report = L4Report(
            verdict="fail",
            scenario_results=[
                ScenarioResult(scenario_id="s1", attempted=["x"], outcome="fail"),
            ],
            findings=[
                Finding(
                    what="Low severity issue",
                    where=["src/x.ts"],
                    why="Observed low problem here",
                    severity="low",
                    scenario_id="s1",
                ),
                Finding(
                    what="High severity issue",
                    where=["src/y.ts"],
                    why="Observed high problem",
                    severity="high",
                    scenario_id="s1",
                ),
            ],
        )
        mock_session = MagicMock()

        with patch("services.evaluator.l4_runner.outbox_emit") as mock_emit:
            _emit_l4_findings(mock_session, "db_url", "run_p_001", "plan_001", "proj_001", report)

            mock_emit.assert_called_once()
            args = mock_emit.call_args
            emitted = args[0][1]
            # Only the 'high' finding should be included
            assert len(emitted.findings) == 1
            assert emitted.findings[0]["severity"] == "high"
            assert emitted.run_id == "run_p_001"
            assert emitted.plan_id == "plan_001"
            assert emitted.labeled_by == "harness"

    def test_empty_above_floor_still_emits(self):
        """No qualifying findings → call emit with empty findings list."""
        report = L4Report(
            verdict="fail",
            scenario_results=[
                ScenarioResult(scenario_id="s1", attempted=["x"], outcome="fail"),
            ],
            findings=[
                Finding(
                    what="Low severity issue here",
                    where=["src/x.ts"],
                    why="Observed low problem detected",
                    severity="low",
                    scenario_id="s1",
                ),
            ],
        )
        mock_session = MagicMock()

        with patch("services.evaluator.l4_runner.outbox_emit") as mock_emit:
            _emit_l4_findings(mock_session, "db_url", "run_p_001", "plan_001", "proj_001", report)

            mock_emit.assert_called_once()
            args = mock_emit.call_args
            emitted = args[0][1]
            assert len(emitted.findings) == 0

    def test_custom_labeled_by(self):
        report = L4Report(
            verdict="fail",
            scenario_results=[
                ScenarioResult(scenario_id="s1", attempted=["x"], outcome="fail"),
            ],
            findings=[
                Finding(
                    what="High issue",
                    where=["src/y.ts"],
                    why="Observed high problem",
                    severity="high",
                    scenario_id="s1",
                ),
            ],
        )
        mock_session = MagicMock()

        with patch("services.evaluator.l4_runner.outbox_emit") as mock_emit:
            _emit_l4_findings(mock_session, "db_url", "run_p_001", "plan_001", "proj_001",
                              report, labeled_by="human")
            emitted = mock_emit.call_args[0][1]
            assert emitted.labeled_by == "human"

    def test_emit_exception_does_not_raise(self):
        """Exception in outbox_emit is caught and logged, never propagated."""
        report = L4Report(
            verdict="fail",
            scenario_results=[
                ScenarioResult(scenario_id="s1", attempted=["x"], outcome="fail"),
            ],
            findings=[
                Finding(
                    what="High issue",
                    where=["src/y.ts"],
                    why="Observed high problem",
                    severity="high",
                    scenario_id="s1",
                ),
            ],
        )
        mock_session = MagicMock()

        with patch("services.evaluator.l4_runner.outbox_emit", side_effect=RuntimeError("boom")):
            # Should not raise
            _emit_l4_findings(mock_session, "db_url", "run_p_001", "plan_001", "proj_001", report)

    def test_model_dump_format(self):
        """The finding model_dump is called (not raw Finding objects)."""
        report = L4Report(
            verdict="fail",
            scenario_results=[
                ScenarioResult(scenario_id="s1", attempted=["x"], outcome="fail"),
            ],
            findings=[
                Finding(
                    what="High issue",
                    where=["src/y.ts"],
                    why="Observed high problem",
                    severity="high",
                    scenario_id="s1",
                ),
            ],
        )
        mock_session = MagicMock()

        with patch("services.evaluator.l4_runner.outbox_emit") as mock_emit:
            _emit_l4_findings(mock_session, "db_url", "run_p_001", "plan_001", "proj_001", report)
            emitted = mock_emit.call_args[0][1]
            assert isinstance(emitted.findings, list)
            assert isinstance(emitted.findings[0], dict)
            assert emitted.findings[0]["what"] == "High issue"


# ── SEVERITY_RANK env override ───────────────────────────────────────

class TestMinSeverityConfig:
    def test_default_is_medium(self):
        assert MIN_SEVERITY_RANK == 1  # medium

    def test_low_env_override(self):
        with patch.dict(os.environ, {"L4_MIN_SEVERITY": "low"}, clear=False):
            from importlib import reload
            import services.evaluator.l4_runner as runner_mod
            reload(runner_mod)
            assert runner_mod.MIN_SEVERITY_RANK == 0
        # Reload again to restore default
        from importlib import reload
        import services.evaluator.l4_runner as runner_mod
        reload(runner_mod)

    def test_high_env_override(self):
        with patch.dict(os.environ, {"L4_MIN_SEVERITY": "high"}, clear=False):
            from importlib import reload
            import services.evaluator.l4_runner as runner_mod
            reload(runner_mod)
            assert runner_mod.MIN_SEVERITY_RANK == 2
        from importlib import reload
        import services.evaluator.l4_runner as runner_mod
        reload(runner_mod)
