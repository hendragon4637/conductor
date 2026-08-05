"""Tests for ratify_intent spec/quality_intent propagation and _post_goal payload."""
from __future__ import annotations

from unittest import mock

import pytest


class TestPostGoalIncludesSpec:
    @mock.patch("httpx.post")
    def test_includes_spec_and_quality_intent(self, mock_post):
        """_post_goal adds spec/quality_intent to the /goal payload when present."""
        mock_post.return_value.raise_for_status.return_value = None
        mock_post.return_value.json.return_value = {"plan_id": "plan_x", "status": "generating"}

        from services.intake.main import _post_goal
        result = _post_goal({
            "id": 1,
            "intent_text": "build it",
            "origin": "system_goal",
            "source_ref": "sys:p1",
            "project_id": "p1",
            "evidence": [],
            "spec": "REST API with /invoices",
            "quality_intent": "High reliability",
        })

        assert result["plan_id"] == "plan_x"
        payload = mock_post.call_args.kwargs["json"]
        assert payload["spec"] == "REST API with /invoices"
        assert payload["quality_intent"] == "High reliability"

    @mock.patch("httpx.post")
    def test_omits_spec_when_absent(self, mock_post):
        """_post_goal omits spec/quality_intent keys when not set."""
        mock_post.return_value.raise_for_status.return_value = None
        mock_post.return_value.json.return_value = {"plan_id": "plan_y", "status": "generating"}

        from services.intake.main import _post_goal
        _post_goal({
            "id": 2,
            "intent_text": "build it",
            "origin": "human",
            "source_ref": None,
            "project_id": "default",
            "evidence": [],
        })

        payload = mock_post.call_args.kwargs["json"]
        assert "spec" not in payload
        assert "quality_intent" not in payload


class TestRatifyCarriesSpec:
    @mock.patch("services.intake.main._submit")
    @mock.patch("services.intake.main.load_intent_by_id")
    @mock.patch("psycopg.connect")
    def test_spec_carried_to_submit(self, mock_connect, mock_load, mock_submit):
        """ratify_intent copies spec/quality_intent onto the intent before _submit."""
        from services.intake.main import app
        from fastapi.testclient import TestClient

        mock_load.return_value = {
            "id": 42,
            "status": "proposed",
            "project_id": "sys-abc",
            "intent_text": "build billing",
            "origin": "system_goal",
            "source_ref": None,
            "evidence": [],
            "proposed_project": {
                "project_name": "billing",
                "kind": "service",
                "system_id": "sys-abc",
                "spec": "REST API with /invoices",
                "quality_intent": "High reliability",
            },
        }
        mock_cur = mock.MagicMock()
        mock_conn = mock_connect.return_value.__enter__.return_value
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_submit.return_value = "plan_abc"

        client = TestClient(app)
        resp = client.post("/intake/intents/42/ratify", json={"auto_submit": True})

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ratified"
        assert data["submitted"] is True
        submitted = mock_submit.call_args[0][0]
        assert submitted["spec"] == "REST API with /invoices"
        assert submitted["quality_intent"] == "High reliability"
