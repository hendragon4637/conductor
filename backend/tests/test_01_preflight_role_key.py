"""Gateway role-key authentication preflight tests.

Validates two layers of the Gateway role-key auth pipeline:

1. **Conductor-side Gateway client** (``backend.llm.gateway.call``) — verifies that
   the ``ROLE_KEY_ENV`` mapping correctly injects ``Authorization: Bearer`` headers
   into the upstream HTTP request. Transport layer is mocked.

2. **LiteLLM Gateway HTTP API** — end-to-end tests against the live LiteLLM proxy
   at ``http://localhost:4000/v1``.  These are skipped when the proxy is unreachable
   so they never block CI.

[GATE 01 — preflight]
"""
from __future__ import annotations

import json
import os
from unittest import mock

import pytest

# ══════════════════════════════════════════════════════════════════════════════
# 1.  Conductor Gateway Client unit tests
# ══════════════════════════════════════════════════════════════════════════════


class TestGatewayClientRoleKey:
    """Role-key logic inside ``backend.llm.gateway.call()``.

    The HTTP transport (``urllib.request.urlopen``) is mocked so these tests are
    fast, deterministic, and require no external service.
    """

    # ── helper ──────────────────────────────────────────────────────────

    @staticmethod
    def _mock_http_response(body: dict | None = None):
        """Return a ``MagicMock`` that acts as a ``urlopen`` response."""
        payload = body or {
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"total_tokens": 5},
        }
        mock_resp = mock.MagicMock()
        mock_resp.__enter__.return_value.read.return_value = json.dumps(payload).encode()
        return mock_resp

    # ── valid-role scenarios ────────────────────────────────────────────

    def test_planning_role_sets_bearer_header(self):
        """``call('meta_planner', …)`` reads ``LITELLM_KEY_PLANNING`` and sets
        ``Authorization: Bearer <key>``."""
        from backend.llm.gateway import call

        with mock.patch.dict(os.environ, {"LITELLM_KEY_PLANNING": "sk-planning-test"}, clear=True):
            with mock.patch("urllib.request.urlopen") as urlopen:
                urlopen.return_value = self._mock_http_response()
                call("meta_planner", [{"role": "user", "content": "hi"}])

                req = urlopen.call_args[0][0]
                assert req.headers.get("Authorization") == "Bearer sk-planning-test"

    def test_evaluation_role_sets_bearer_header(self):
        """``call('l2_judge', …)`` reads ``LITELLM_KEY_EVALUATION``."""
        from backend.llm.gateway import call

        with mock.patch.dict(os.environ, {"LITELLM_KEY_EVALUATION": "sk-eval-test"}, clear=True):
            with mock.patch("urllib.request.urlopen") as urlopen:
                urlopen.return_value = self._mock_http_response()
                call("l2_judge", [{"role": "user", "content": "hi"}])

                req = urlopen.call_args[0][0]
                assert req.headers.get("Authorization") == "Bearer sk-eval-test"

    def test_execution_role_sets_bearer_header(self):
        """``call('execution', …)`` reads ``LITELLM_KEY_EXECUTION``."""
        from backend.llm.gateway import call

        with mock.patch.dict(os.environ, {"LITELLM_KEY_EXECUTION": "sk-exec-test"}, clear=True):
            with mock.patch("urllib.request.urlopen") as urlopen:
                urlopen.return_value = self._mock_http_response()
                call("execution", [{"role": "user", "content": "hi"}])

                req = urlopen.call_args[0][0]
                assert req.headers.get("Authorization") == "Bearer sk-exec-test"

    def test_plan_brain_role_sets_bearer_header(self):
        """``call('plan_brain', …)`` also reads ``LITELLM_KEY_PLANNING``."""
        from backend.llm.gateway import call

        with mock.patch.dict(os.environ, {"LITELLM_KEY_PLANNING": "sk-brain-test"}, clear=True):
            with mock.patch("urllib.request.urlopen") as urlopen:
                urlopen.return_value = self._mock_http_response()
                call("plan_brain", [{"role": "user", "content": "hi"}])

                req = urlopen.call_args[0][0]
                assert req.headers.get("Authorization") == "Bearer sk-brain-test"

    # ── missing / unknown role scenarios ────────────────────────────────

    def test_unknown_role_no_auth_header(self):
        """An unrecognised role sends **no** ``Authorization`` header."""
        from backend.llm.gateway import call

        with mock.patch("urllib.request.urlopen") as urlopen:
            urlopen.return_value = self._mock_http_response()
            call("bogus_role", [{"role": "user", "content": "hi"}])

            req = urlopen.call_args[0][0]
            assert "Authorization" not in req.headers

    def test_missing_env_var_no_auth_header(self):
        """A known role whose env var is unset sends no ``Authorization`` header."""
        from backend.llm.gateway import call

        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch("urllib.request.urlopen") as urlopen:
                urlopen.return_value = self._mock_http_response()
                call("execution", [{"role": "user", "content": "hi"}])

                req = urlopen.call_args[0][0]
                assert "Authorization" not in req.headers

    def test_empty_env_var_no_auth_header(self):
        """A known role whose env var is set to empty string sends no auth header."""
        from backend.llm.gateway import call

        with mock.patch.dict(os.environ, {"LITELLM_KEY_PLANNING": ""}, clear=True):
            with mock.patch("urllib.request.urlopen") as urlopen:
                urlopen.return_value = self._mock_http_response()
                call("meta_planner", [{"role": "user", "content": "hi"}])

                req = urlopen.call_args[0][0]
                assert "Authorization" not in req.headers

    # ── error propagation ───────────────────────────────────────────────

    def test_gateway_unreachable_raises_runtime_error(self):
        """When the upstream gateway is unreachable a ``RuntimeError`` is raised."""
        from backend.llm.gateway import call

        with mock.patch("urllib.request.urlopen") as urlopen:
            urlopen.side_effect = Exception("Connection refused")

            with pytest.raises(RuntimeError, match="LiteLLM gateway call failed"):
                call("meta_planner", [{"role": "user", "content": "hi"}])

    def test_gateway_http_error_raises_runtime_error(self):
        """When the upstream gateway returns a non-200 a ``RuntimeError`` is raised."""
        from backend.llm.gateway import call

        with mock.patch("urllib.request.urlopen") as urlopen:
            urlopen.side_effect = Exception("HTTP Error 403: Forbidden")

            with pytest.raises(RuntimeError, match="LiteLLM gateway call failed"):
                call("meta_planner", [{"role": "user", "content": "hi"}])

    # ── configuration integrity ─────────────────────────────────────────

    def test_all_roles_have_model_mapping(self):
        """Every role in ``ROLE_KEY_ENV`` also appears in ``ROLE_MODEL``."""
        from backend.llm.gateway import ROLE_KEY_ENV, ROLE_MODEL

        for role in ROLE_KEY_ENV:
            assert role in ROLE_MODEL, f"{role!r} missing from ROLE_MODEL"

    def test_all_roles_have_env_var_mapping(self):
        """Every role in ``ROLE_MODEL`` also appears in ``ROLE_KEY_ENV``."""
        from backend.llm.gateway import ROLE_KEY_ENV, ROLE_MODEL

        for role in ROLE_MODEL:
            assert role in ROLE_KEY_ENV, f"{role!r} missing from ROLE_KEY_ENV"

    def test_response_usage_is_accumulated(self):
        """``call()`` accumulates ``total_tokens`` returned by the gateway into
        the module-level ``_USAGE`` dict."""
        from backend.llm.gateway import call, reset_usage, get_usage

        reset_usage()

        with mock.patch.dict(os.environ, {"LITELLM_KEY_PLANNING": "sk-test"}, clear=True):
            with mock.patch("urllib.request.urlopen") as urlopen:
                urlopen.return_value = self._mock_http_response(
                    {"choices": [{"message": {"content": "ok"}}], "usage": {"total_tokens": 42}}
                )
                call("meta_planner", [{"role": "user", "content": "hi"}])

        usage = get_usage("meta_planner")
        assert usage.get("meta_planner") == 42


# ══════════════════════════════════════════════════════════════════════════════
# 2.  LiteLLM Gateway HTTP integration tests
# ══════════════════════════════════════════════════════════════════════════════


def _litellm_reachable() -> bool:
    """Return ``True`` if the LiteLLM proxy health endpoint responds."""
    import urllib.request

    try:
        req = urllib.request.Request(
            "http://localhost:4000/health/readiness", method="GET"
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.status == 200
    except Exception:
        return False


LITELLM_REACHABLE = _litellm_reachable()


def _require_litellm():
    """Skip the test if LiteLLM is not running."""
    if not LITELLM_REACHABLE:
        pytest.skip("LiteLLM proxy not reachable at http://localhost:4000")


class TestLiteLlmGatewayIntegration:
    """End-to-end role-key validation against the real LiteLLM Gateway.

    These tests send actual HTTP requests to ``http://localhost:4000/v1``.
    They are **skipped** when the proxy is unreachable, so they never block CI
    on a fresh checkout or in environments without Docker.
    """

    # ── helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def _post(body: dict, api_key: str | None = None) -> tuple[int, dict]:
        """POST to ``/v1/chat/completions`` and return ``(status, body_dict)``."""
        import urllib.request

        headers = {"Content-Type": "application/json"}
        if api_key is not None:
            headers["Authorization"] = f"Bearer {api_key}"

        req = urllib.request.Request(
            "http://localhost:4000/v1/chat/completions",
            data=json.dumps(body).encode(),
            headers=headers,
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.status, json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode())

    # ── valid auth ──────────────────────────────────────────────────────

    @pytest.fixture(autouse=True)
    def _ensure_litellm(self):
        _require_litellm()

    def test_valid_planning_key_with_planning_model(self):
        """A valid planning virtual key → 200 when model is in the key's scope."""
        api_key = os.environ.get("LITELLM_KEY_PLANNING")
        if not api_key:
            pytest.skip("LITELLM_KEY_PLANNING not set in environment")

        status, body = self._post(
            {"model": "deepseek-planning", "messages": [{"role": "user", "content": "say hi"}], "max_tokens": 10},
            api_key=api_key,
        )
        assert status == 200, f"Expected 200, got {status}: {body}"
        assert "choices" in body
        assert body["object"] == "chat.completion"

    def test_valid_execution_key_with_execution_model(self):
        """A valid execution virtual key → 200 when model is in the key's scope."""
        api_key = os.environ.get("LITELLM_KEY_EXECUTION")
        if not api_key:
            pytest.skip("LITELLM_KEY_EXECUTION not set in environment")

        status, body = self._post(
            {"model": "gptoss-exec", "messages": [{"role": "user", "content": "say hi"}], "max_tokens": 10},
            api_key=api_key,
        )
        assert status == 200, f"Expected 200, got {status}: {body}"

    def test_valid_evaluation_key_with_judge_model(self):
        """A valid evaluation virtual key → 200 when model is in the key's scope."""
        api_key = os.environ.get("LITELLM_KEY_EVALUATION")
        if not api_key:
            pytest.skip("LITELLM_KEY_EVALUATION not set in environment")

        status, body = self._post(
            {"model": "judge", "messages": [{"role": "user", "content": "say hi"}], "max_tokens": 10},
            api_key=api_key,
        )
        assert status == 200, f"Expected 200, got {status}: {body}"

    # ── cross-role denial ───────────────────────────────────────────────

    def test_planning_key_denied_for_execution_model(self):
        """A planning key used with ``gptoss-exec`` → 403."""
        api_key = os.environ.get("LITELLM_KEY_PLANNING")
        if not api_key:
            pytest.skip("LITELLM_KEY_PLANNING not set in environment")

        status, body = self._post(
            {"model": "gptoss-exec", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 10},
            api_key=api_key,
        )
        assert status == 403, f"Expected 403, got {status}"
        assert "key_model_access_denied" in body.get("error", {}).get("type", "")

    def test_execution_key_denied_for_planning_model(self):
        """An execution key used with ``deepseek-planning`` → 403."""
        api_key = os.environ.get("LITELLM_KEY_EXECUTION")
        if not api_key:
            pytest.skip("LITELLM_KEY_EXECUTION not set in environment")

        status, body = self._post(
            {"model": "deepseek-planning", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 10},
            api_key=api_key,
        )
        assert status == 403, f"Expected 403, got {status}"
        assert "key_model_access_denied" in body.get("error", {}).get("type", "")

    def test_evaluation_key_denied_for_planning_model(self):
        """An evaluation key used with ``deepseek-planning`` → 403."""
        api_key = os.environ.get("LITELLM_KEY_EVALUATION")
        if not api_key:
            pytest.skip("LITELLM_KEY_EVALUATION not set in environment")

        status, body = self._post(
            {"model": "deepseek-planning", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 10},
            api_key=api_key,
        )
        assert status == 403, f"Expected 403, got {status}"
        assert "key_model_access_denied" in body.get("error", {}).get("type", "")

    # ── invalid / missing key ───────────────────────────────────────────

    def test_invalid_key_returns_401(self):
        """A fake/bogus API key → 401."""
        status, body = self._post(
            {"model": "deepseek-planning", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 10},
            api_key="sk-fake-nonexistent-key",
        )
        assert status == 401, f"Expected 401, got {status}"
        assert "token_not_found_in_db" in body.get("error", {}).get("type", "")

    def test_missing_auth_header_returns_401(self):
        """No ``Authorization`` header → 401."""
        status, body = self._post(
            {"model": "deepseek-planning", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 10},
            api_key=None,
        )
        assert status == 401, f"Expected 401, got {status}"
        assert "auth_error" in body.get("error", {}).get("type", "")

    # ── revoked / expired key ───────────────────────────────────────────

    def test_revoked_key_returns_401(self):
        """A key that is not (or no longer) in the LiteLLM DB → 401.

        LiteLLM validates virtual keys against its ``LiteLLM_VerificationTokenTable``.
        A key that was revoked (deleted from the table) is indistinguishable from
        an unknown key from the caller's perspective — both return ``401`` with
        ``token_not_found_in_db``.
        """
        status, body = self._post(
            {"model": "deepseek-planning", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 10},
            api_key="sk-revoked-or-never-existed",
        )
        assert status == 401, f"Expected 401, got {status}"
        assert "token_not_found_in_db" in body.get("error", {}).get("type", "")
