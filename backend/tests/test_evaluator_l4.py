"""Tests for L4 persona/usage simulation — use the product as a user."""
from __future__ import annotations

import json
import urllib.error
from pathlib import Path
from unittest import mock

import pytest
import yaml

from backend.evaluator.l4_persona.simulate import (
    L4Report,
    StepObservation,
    _check_expectations,
    _execute_behavior,
    _execute_http_step,
    _score_discoverability,
    _score_error_feedback,
    _score_friction,
    load_persona,
    run_l4,
)
from backend.evaluator.l4_persona.simulate import BehaviorResult


# ── Persona loading ──────────────────────────────────────────────────────────


def test_load_persona_exists():
    persona = load_persona("casual_user")
    assert persona["name"] == "casual_user"
    assert "goal" in persona
    assert "behaviors" in persona
    assert len(persona["behaviors"]) >= 1


def test_load_persona_not_found():
    with pytest.raises(FileNotFoundError):
        load_persona("nonexistent_persona")


def test_persona_yaml_is_valid(tmp_path):
    """Verify the casual_user persona YAML is syntactically valid."""
    path = Path(__file__).parent.parent / "evaluator" / "l4_persona" / "personas" / "casual_user.yaml"
    assert path.exists()
    with open(path) as f:
        data = yaml.safe_load(f)
    assert isinstance(data, dict)
    assert "name" in data
    assert "behaviors" in data
    for b in data["behaviors"]:
        assert "id" in b
        assert "steps" in b
        for s in b["steps"]:
            assert "action" in s


# ── Step execution ───────────────────────────────────────────────────────────


def test_execute_http_step_success():
    """A 200 response should populate status_code and body."""
    step = {"method": "GET", "path": "/health", "action": "request"}

    class FakeResponse:
        status = 200

        def read(self):
            return b'{"status": "ok"}'

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    with mock.patch("urllib.request.urlopen", return_value=FakeResponse()):
        obs = _execute_http_step(step, "http://localhost:9999", {})

    assert obs.status_code == 200
    assert "ok" in obs.response_body
    assert obs.error is None


def test_execute_http_step_http_error():
    """A 4xx response should capture status and body."""
    step = {"method": "GET", "path": "/nonexistent", "action": "request"}

    def _fake_urlopen(*args, **kwargs):
        raise urllib.error.HTTPError(
            url="/nonexistent", code=404, msg="Not Found",
            hdrs={}, fp=None,
        )

    with mock.patch("urllib.request.urlopen", side_effect=_fake_urlopen):
        obs = _execute_http_step(step, "http://localhost:9999", {})

    assert obs.status_code == 404


def test_execute_http_step_connection_error():
    """A connection error should set error field."""
    step = {"method": "GET", "path": "/health", "action": "request"}

    with mock.patch("urllib.request.urlopen", side_effect=ConnectionError("refused")):
        obs = _execute_http_step(step, "http://localhost:9999", {})

    assert obs.error is not None
    assert obs.status_code is None


# ── Expectation checking ────────────────────────────────────────────────────


def test_check_expectations_status_in_pass():
    step = {"action": "request", "method": "GET", "path": "/test",
            "expect": {"status_in": [200, 201]}}
    obs = StepObservation(action="request", method="GET", path="/test",
                          status_code=200)
    assert _check_expectations(step, obs) is True


def test_check_expectations_status_in_fail():
    step = {"action": "request", "method": "GET", "path": "/test",
            "expect": {"status_in": [200]}}
    obs = StepObservation(action="request", method="GET", path="/test",
                          status_code=404)
    assert _check_expectations(step, obs) is False


def test_check_expectations_body_contains():
    step = {"action": "request", "method": "GET", "path": "/test",
            "expect": {"body_contains": "id"}}
    obs = StepObservation(action="request", method="GET", path="/test",
                          status_code=200, response_body='{"id": 1}')
    assert _check_expectations(step, obs) is True


def test_check_expectations_body_is_array():
    step = {"action": "request", "method": "GET", "path": "/list",
            "expect": {"body_is_array": True}}
    obs = StepObservation(action="request", method="GET", path="/list",
                          status_code=200, response_body="[1, 2, 3]")
    assert _check_expectations(step, obs) is True


def test_check_expectations_body_not_empty():
    step = {"action": "request", "method": "GET", "path": "/error",
            "expect": {"body_not_empty": True}}
    obs = StepObservation(action="request", method="GET", path="/error",
                          status_code=400, response_body='{"error": "bad"}')
    assert _check_expectations(step, obs) is True

    obs2 = StepObservation(action="request", method="GET", path="/error",
                           status_code=400, response_body="")
    assert _check_expectations(step, obs2) is False


# ── Behavior execution ──────────────────────────────────────────────────────


def test_execute_behavior_all_pass():
    behavior = {
        "id": "test_behavior",
        "description": "Test",
        "steps": [
            {"action": "request", "method": "GET", "path": "/ok",
             "expect": {"status_in": [200]}},
        ],
    }

    class FakeResp:
        status = 200

        def read(self):
            return b"ok"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    with mock.patch("urllib.request.urlopen", return_value=FakeResp()):
        result = _execute_behavior(behavior, "http://localhost:9999", {})

    assert result.success is True
    assert result.friction_score == 0.0


def test_execute_behavior_some_fail():
    behavior = {
        "id": "test_fail",
        "description": "Should fail",
        "steps": [
            {"action": "request", "method": "GET", "path": "/fail",
             "expect": {"status_in": [200]}},
        ],
    }

    def _fail(*args, **kwargs):
        raise urllib.error.HTTPError(
            url="/fail", code=500, msg="Error", hdrs={}, fp=None,
        )

    with mock.patch("urllib.request.urlopen", side_effect=_fail):
        result = _execute_behavior(behavior, "http://localhost:9999", {})

    assert result.success is False
    assert result.friction_score > 0.0


# ── Scoring ──────────────────────────────────────────────────────────────────


def test_score_discoverability_no_failures():
    results = [
        BehaviorResult(behavior_id="a", description="a", success=True),
        BehaviorResult(behavior_id="b", description="b", success=True),
    ]
    assert _score_discoverability(results) == 0.0


def test_score_discoverability_some_failures():
    results = [
        BehaviorResult(behavior_id="a", description="a", success=True),
        BehaviorResult(behavior_id="b", description="b", success=False),
    ]
    assert _score_discoverability(results) == 0.5


def test_score_error_feedback_empty():
    assert _score_error_feedback([]) == 0.0


def test_score_error_feedback_no_error_related():
    results = [
        BehaviorResult(behavior_id="add_valid", description="add", success=True),
    ]
    assert _score_error_feedback(results) == 0.0


def test_score_friction_all_smooth():
    results = [
        BehaviorResult(behavior_id="a", description="a", friction_score=0.0),
        BehaviorResult(behavior_id="b", description="b", friction_score=0.0),
    ]
    assert _score_friction(results) == 0.0


def test_score_friction_mixed():
    results = [
        BehaviorResult(behavior_id="a", description="a", friction_score=0.0),
        BehaviorResult(behavior_id="b", description="b", friction_score=0.5),
    ]
    assert _score_friction(results) == 0.25


# ── run_l4 integration ──────────────────────────────────────────────────────


def test_run_l4_connection_error():
    """Server unreachable raises ConnectionError."""
    error = urllib.error.URLError("Connection refused")
    with mock.patch("urllib.request.urlopen", side_effect=error):
        with pytest.raises(ConnectionError):
            run_l4("casual_user", base_url="http://localhost:1")


def test_run_l4_returns_report():
    """With mocked HTTP responses, run_l4 should return a valid L4Report."""

    class FakeResp:
        status = 200
        _data = b'[{"id": 1, "amount": 1000}]'

        def read(self):
            return self._data

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    call_count = 0

    def _fake_urlopen(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        resp = FakeResp()
        if call_count == 1:
            resp._data = b'{"id": 42, "amount": 15000}'
        elif call_count == 3:
            raise urllib.error.HTTPError(
                url="/api/transactions", code=422, msg="Unprocessable",
                hdrs={}, fp=None,
            )
        elif call_count == 5:
            resp.status = 204
            resp._data = b""
        elif call_count == 6:
            raise urllib.error.HTTPError(
                url="/api/transactions/42", code=404, msg="Not Found",
                hdrs={}, fp=None,
            )
        return resp

    with mock.patch("urllib.request.urlopen", side_effect=_fake_urlopen):
        with mock.patch("backend.evaluator.l4_persona.simulate._log_to_langfuse"):
            report = run_l4("casual_user", base_url="http://localhost:9999")

    assert isinstance(report, L4Report)
    assert report.persona_name == "casual_user"
    assert len(report.behaviors) >= 1
    assert "discoverability" in report.dimensions
    assert "friction" in report.dimensions
    assert 0.0 <= report.overall_friction <= 1.0


# ── Boundary tests ──────────────────────────────────────────────────────────


def test_l4_does_not_auto_decide_direction():
    """L4 produces a friction report but does NOT auto-decide feature direction.
    The report is surfaced for human review (simulated here by checking
    that report.dimensions exist without an auto-apply flag)."""
    report = L4Report(
        persona_name="test",
        goal="test goal",
        dimensions={"discoverability": 0.5, "friction": 0.3},
        overall_friction=0.4,
    )
    assert isinstance(report.dimensions, dict)
    assert "discoverability" in report.dimensions
    assert not hasattr(report, "auto_apply")
    assert not hasattr(report, "decision")
