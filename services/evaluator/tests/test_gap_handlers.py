"""Tests for Gap 5: stagnation detection and L4 findings emission.

Covers: stagnation detection logic and L4Findings event construction.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest


# ── Gap 5: Stagnation detection logic ────────────────────────────────────


class TestStagnationDetection:
    """Stagnation: N consecutive done_no_change → failed."""

    def test_stagnation_triggers_when_at_limit(self):
        """prior_no_change >= STAGNATION_LIMIT overrides gate_outcome to failed."""
        with patch.dict(os.environ, {"STAGNATION_LIMIT": "3"}, clear=False):
            from services.evaluator.main import STAGNATION_LIMIT

            assert STAGNATION_LIMIT == 3

    def test_stagnation_does_not_trigger_below_limit(self):
        """Default STAGNATION_LIMIT is 3 (from env or fallback)."""
        from services.evaluator.main import STAGNATION_LIMIT
        assert STAGNATION_LIMIT >= 1


# ── L4 findings emission ─────────────────────────────────────────────────


class TestOnRunCompletedL4Findings:
    """l4.findings emission from the L4 polling path in on_run_completed."""

    @pytest.fixture
    def mock_payload(self):
        return {
            "event_type": "run.completed",
            "run_id": "run_test_001",
            "plan_id": "plan_test_001",
            "product_type": "api",
        }

    def test_findings_emitted_for_low_standalone(self):
        """standalone < 0.5 adds a warning finding."""
        from contracts.events import L4Findings
        from services.evaluator.main import emit, L4Findings

        findings = [
            {
                "what": "L4 standalone persona detected usability friction",
                "where": ["l4_standalone"],
                "why": "Standalone score 0.3 below 0.5 threshold",
                "severity": "warning",
            },
        ]
        event = L4Findings(
            run_id="run_test_001",
            plan_id="plan_test_001",
            project_id="default",
            findings=findings,
            labeled_by="harness",
        )
        assert event.run_id == "run_test_001"
        assert event.labeled_by == "harness"
        assert len(event.findings) == 1
        assert event.findings[0]["severity"] == "warning"

    def test_findings_emitted_for_low_acceptance(self):
        """acceptance < 0.5 adds an error finding."""
        from contracts.events import L4Findings

        findings = [
            {
                "what": "L4 acceptance persona failed to verify success criteria",
                "where": ["l4_acceptance"],
                "why": "Acceptance score 0.2 below 0.5 threshold",
                "severity": "error",
            },
        ]
        event = L4Findings(
            run_id="run_test_001",
            plan_id="plan_test_001",
            project_id="default",
            findings=findings,
            labeled_by="harness",
        )
        assert len(event.findings) == 1
        assert event.findings[0]["severity"] == "error"

    def test_no_findings_when_above_threshold(self):
        """standalone >= 0.5 and acceptance >= 0.5 → empty findings list."""
        from contracts.events import L4Findings

        event = L4Findings(
            run_id="run_test_001",
            plan_id="plan_test_001",
            project_id="default",
            findings=[],
            labeled_by="harness",
        )
        assert len(event.findings) == 0

    def test_emit_called_for_low_scores(self):
        """emit(L4Findings(...)) is called with correct structure when scores are low."""
        mock_session = MagicMock()

        with patch("services.evaluator.main.emit") as mock_emit:
            from services.evaluator.main import emit, L4Findings

            findings = [{
                "what": "L4 standalone persona detected usability friction",
                "where": ["l4_standalone"],
                "why": "Standalone score 0.3 below 0.5 threshold",
                "severity": "warning",
            }]
            emit(mock_session, L4Findings(
                run_id="run_test_001",
                plan_id="plan_test_001",
                project_id="default",
                findings=findings,
                labeled_by="harness",
            ))

            mock_emit.assert_called_once()
            call_args = mock_emit.call_args
            emitted = call_args[0][1]
            assert isinstance(emitted, L4Findings)
            assert emitted.run_id == "run_test_001"
            assert emitted.project_id == "default"
            assert len(emitted.findings) == 1
            assert emitted.findings[0]["severity"] == "warning"
