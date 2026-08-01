"""Tests for on_system_goal_queued — the intake handler for sys.goal_queued events."""
from __future__ import annotations

from unittest import mock

import pytest


class TestOnSystemGoalQueued:
    @mock.patch("services.intake.main._submit")
    @mock.patch("psycopg.connect")
    def test_submits_intent_and_updates_pending(self, mock_connect, mock_submit):
        """Successful submission → pending_goals set to submitted with plan_id."""
        mock_submit.return_value = "plan_abc"
        mock_cur = mock.MagicMock()
        mock_conn = mock_connect.return_value.__enter__.return_value
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        from services.intake.main import on_system_goal_queued
        on_system_goal_queued(None, {
            "project_id": "p1",
            "raw_input": "build project p1",
            "origin": "system_goal",
        })

        mock_submit.assert_called_once_with({
            "origin": "system_goal",
            "source_ref": "sys:p1",
            "project_id": "p1",
            "intent_text": "build project p1",
            "evidence": [],
            "attempt": 1,
        })
        # Verify pending_goals was updated: status='submitted', plan_id set
        update_calls = [
            c for c in mock_cur.execute.call_args_list
            if "UPDATE pending_goals" in str(c[0][0])
        ]
        assert len(update_calls) == 1
        assert "submitted" in str(update_calls[0])
        assert "plan_abc" in str(update_calls[0])

    @mock.patch("services.intake.main._submit")
    @mock.patch("psycopg.connect")
    def test_sets_last_error_on_failure(self, mock_connect, mock_submit):
        """Failed submission → pending_goals gets last_error."""
        mock_submit.return_value = None
        mock_cur = mock.MagicMock()
        mock_conn = mock_connect.return_value.__enter__.return_value
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        from services.intake.main import on_system_goal_queued
        on_system_goal_queued(None, {
            "project_id": "p1",
            "raw_input": "build it",
            "origin": "system_goal",
        })

        update_calls = [
            c for c in mock_cur.execute.call_args_list
            if "UPDATE pending_goals" in str(c[0][0])
        ]
        assert len(update_calls) == 1
        assert "last_error" in str(update_calls[0])

    @mock.patch("services.intake.main._submit")
    def test_skips_on_missing_fields(self, mock_submit):
        """Missing project_id or raw_input → no-op."""
        from services.intake.main import on_system_goal_queued

        on_system_goal_queued(None, {"project_id": "", "raw_input": "x"})
        mock_submit.assert_not_called()

        on_system_goal_queued(None, {"project_id": "p1", "raw_input": ""})
        mock_submit.assert_not_called()


class TestSubmitReturnValue:
    """_submit() now returns plan_id so callers can update pending_goals."""

    @mock.patch("services.intake.main._post_goal")
    @mock.patch("services.intake.main.insert_intent")
    @mock.patch("services.intake.main._paused")
    @mock.patch("services.intake.main.is_duplicate")
    @mock.patch("services.intake.main._over_rate_limit")
    @mock.patch("services.intake.main._project_free")
    def test_returns_plan_id_on_success(
        self, mock_free, mock_rate, mock_dup, mock_paused, mock_insert, mock_post,
    ):
        """Successful /goal call → returns plan_id."""
        mock_paused.return_value = False
        mock_dup.return_value = False
        mock_rate.return_value = False
        mock_free.return_value = True
        mock_insert.return_value = {"id": 1, "project_id": "p1"}
        mock_post.return_value = {"plan_id": "plan_abc", "status": "generating"}

        from services.intake.main import _submit
        result = _submit({"project_id": "p1", "intent_text": "build it", "origin": "test", "source_ref": None, "evidence": []})

        assert result == "plan_abc"

    @mock.patch("services.intake.main.insert_intent")
    @mock.patch("services.intake.main._paused")
    def test_returns_none_when_paused(self, mock_paused, mock_insert):
        """Paused project → returns None."""
        mock_paused.return_value = True

        from services.intake.main import _submit
        result = _submit({"project_id": "p1", "intent_text": "x", "origin": "test", "source_ref": None, "evidence": []})

        assert result is None

    @mock.patch("services.intake.main._post_goal")
    @mock.patch("services.intake.main.insert_intent")
    @mock.patch("services.intake.main._paused")
    @mock.patch("services.intake.main.is_duplicate")
    @mock.patch("services.intake.main._over_rate_limit")
    @mock.patch("services.intake.main._project_free")
    def test_returns_none_on_http_error(
        self, mock_free, mock_rate, mock_dup, mock_paused, mock_insert, mock_post,
    ):
        """/goal HTTP error → returns None."""
        mock_paused.return_value = False
        mock_dup.return_value = False
        mock_rate.return_value = False
        mock_free.return_value = True
        mock_insert.return_value = {"id": 1, "project_id": "p1"}
        mock_post.side_effect = Exception("500 Server Error")

        from services.intake.main import _submit
        result = _submit({"project_id": "p1", "intent_text": "build it", "origin": "test", "source_ref": None, "evidence": []})

        assert result is None


class TestSystemGoalQueuedDispatch:
    """Verify the _dispatch function routes sys.goal_queued payloads correctly."""

    @mock.patch("services.intake.main.on_system_goal_queued")
    def test_routes_to_handler(self, mock_handler):
        """Payload with raw_input+project_id and no questions → handler fires."""
        from services.intake.main import _dispatch as dispatch

        dispatch(None, {
            "project_id": "p1",
            "raw_input": "build it",
            "origin": "system_goal",
        })

        mock_handler.assert_called_once()

    @mock.patch("services.intake.main.on_clarification_needed")
    @mock.patch("services.intake.main.on_system_goal_queued")
    def test_clarification_not_routed_to_system_goal(self, mock_sys, mock_clarify):
        """Payload WITH questions → NOT routed to on_system_goal_queued."""
        from services.intake.main import _dispatch as dispatch

        dispatch(None, {
            "plan_id": "plan_1",
            "project_id": "p1",
            "questions": ["what db?"],
        })

        mock_sys.assert_not_called()
        mock_clarify.assert_called_once()
