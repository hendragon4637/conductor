"""Tests for the stale-intent sweep (guide 08.6)."""
from __future__ import annotations

from unittest import mock

import pytest


class TestSweepStaleIntentsStore:
    def test_escalates_idle_submitted_clarifying(self):
        """Rows idle past the cap are escalated with a reason."""
        mock_cur = mock.MagicMock()
        mock_cur.fetchall.return_value = [
            {"id": 1, "project_id": "p1", "origin": "run_failed", "status": "escalated", "updated_at": None},
            {"id": 2, "project_id": "p2", "origin": "l4_findings", "status": "escalated", "updated_at": None},
        ]
        mock_conn = mock.patch("psycopg.connect").start()
        mock_conn.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value = mock_cur

        from services.intake.store import sweep_stale_intents

        rows = sweep_stale_intents(max_age_hours=24)

        assert len(rows) == 2
        sql = mock_cur.execute.call_args[0][0]
        assert "status IN ('submitted', 'clarifying')" in sql
        assert "make_interval" in sql
        assert "escalated" in sql
        assert mock_cur.execute.call_args[0][1][0] == "stale sweep: no terminal event within 24h"
        assert mock_cur.execute.call_args[0][1][1] == 24
        mock.patch.stopall()

    def test_no_stale_rows_returns_empty(self):
        """Nothing idle → sweep returns empty list."""
        mock_cur = mock.MagicMock()
        mock_cur.fetchall.return_value = []
        mock_conn = mock.patch("psycopg.connect").start()
        mock_conn.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value = mock_cur

        from services.intake.store import sweep_stale_intents

        assert sweep_stale_intents(max_age_hours=24) == []
        mock.patch.stopall()


class TestRunStaleSweep:
    @mock.patch("services.intake.store.sweep_stale_intents")
    def test_returns_count_and_logs_each(self, mock_sweep):
        """run_stale_sweep logs a warning per escalated row."""
        mock_sweep.return_value = [
            {"id": 1, "project_id": "p1", "origin": "run_failed"},
        ]
        from services.intake.main import run_stale_sweep

        with mock.patch("services.intake.main.logger") as mock_logger:
            count = run_stale_sweep(max_age_hours=24)

        assert count == 1
        mock_sweep.assert_called_once_with(max_age_hours=24)
        mock_logger.warning.assert_called_once()
        assert "escalated" in mock_logger.warning.call_args[0][0]

    @mock.patch("services.intake.store.sweep_stale_intents")
    def test_uses_default_hours_from_env(self, mock_sweep):
        """No arg → uses STALE_SWEEP_HOURS default."""
        mock_sweep.return_value = []
        from services.intake.main import run_stale_sweep, STALE_SWEEP_HOURS

        run_stale_sweep()
        mock_sweep.assert_called_once_with(max_age_hours=STALE_SWEEP_HOURS)


class TestStaleSweepLoop:
    @mock.patch("services.intake.main.run_stale_sweep")
    def test_runs_and_swallows_exceptions(self, mock_run):
        """Loop keeps going when the sweep raises."""
        from services.intake.main import stale_sweep_loop

        mock_run.side_effect = [RuntimeError("db down"), None]

        with mock.patch("services.intake.main.time.sleep", side_effect=KeyboardInterrupt):
            with mock.patch("services.intake.main.logger") as mock_logger:
                with pytest.raises(KeyboardInterrupt):
                    stale_sweep_loop()

        assert mock_run.call_count == 1
        mock_logger.exception.assert_called_once()


class TestSweepEndpoint:
    def test_post_sweep_returns_ok(self):
        """POST /intake/sweep triggers the sweep and reports count."""
        from fastapi.testclient import TestClient
        from services.intake.main import app

        with mock.patch("services.intake.main.run_stale_sweep", return_value=2) as mock_run:
            client = TestClient(app)
            resp = client.post("/intake/sweep")
            assert resp.status_code == 200
            assert resp.json() == {"status": "ok", "escalated": 2}
            mock_run.assert_called_once_with(max_age_hours=None)
