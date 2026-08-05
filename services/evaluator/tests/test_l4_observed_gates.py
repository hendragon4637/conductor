"""File 06 — L4 observed-path publish gate, structural retry, run hygiene.

Covers the restored 3-gate emit rule in ``_on_l4_observed`` (guide 06.4),
the one-bounded structural retry (guide 06.3), and ``merge_status='skipped'``
on L4 run creation (guide 06.2).
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from shared.l4_models import Scenario
from services.evaluator import l4_runner


class _FakeQuery:
    def __init__(self, result):
        self._result = result

    def filter(self, *a, **k):
        return self

    def first(self):
        return self._result


class _FakeSession:
    def __init__(self, ns, run):
        self._ns = ns
        self._run = run

    def query(self, model):
        if model.__name__ == "NodeSession":
            return _FakeQuery(self._ns)
        return _FakeQuery(self._run)


def _seeded_dicts():
    return [
        Scenario(id="s1", as_a="user", wants="login", success_looks_like="dashboard").model_dump(),
        Scenario(id="s2", as_a="admin", wants="configure", success_looks_like="saved").model_dump(),
    ]


def _make_worktree(report_dict: dict) -> str:
    tmp = tempfile.mkdtemp(prefix="l4_obs_")
    wt = Path(tmp)
    (wt / "l4_scratch").mkdir(parents=True)
    (wt / "l4_scratch" / "report.json").write_text(json.dumps(report_dict))
    (wt / "src").mkdir()
    (wt / "src" / "login.tsx").write_text("")
    return tmp


def _fail_report() -> dict:
    return {
        "verdict": "fail",
        "scenario_results": [
            {"scenario_id": "s1", "attempted": ["x"], "outcome": "fail"},
            {"scenario_id": "s2", "attempted": ["y"], "outcome": "pass"},
        ],
        "findings": [{
            "what": "Login broken",
            "where": ["src/login.tsx"],
            "why": "Clicked login, no response",
            "severity": "high",
            "scenario_id": "s1",
        }],
        "observations": [],
    }


def _ns(attempt=1, conv=None):
    return SimpleNamespace(
        id="ns_l4_test", role="l4", run_id="l4_r1", worktree="",
        attempt=attempt, aionui_conversation_id=conv,
    )


def _run(worktree: str, scenarios: list | None = None):
    return SimpleNamespace(
        id="l4_r1", project_id="proj_x", plan_id="plan_x",
        parent_run_id="run_p_001", l4_scenarios=scenarios or [],
    )


class TestOnL4ObservedPublishGate:
    def _invoke(self, ns, run):
        return l4_runner._on_l4_observed(_FakeSession(ns, run), {
            "node_session_id": ns.id,
            "verdict": "done",
        })

    def test_ok_negative_report_publishes(self):
        wt = _make_worktree(_fail_report())
        ns, run = _ns(), _run(wt, _seeded_dicts())
        ns.worktree = wt
        with patch.object(l4_runner, "_emit_l4_findings") as m_emit, \
             patch.object(l4_runner, "_persist_l4_success") as m_ok, \
             patch.object(l4_runner, "_persist_l4_failure") as m_fail:
            self._invoke(ns, run)
            m_emit.assert_called_once()
            m_ok.assert_called_once()
            m_fail.assert_not_called()

    def test_verdict_pass_never_publishes(self):
        report = _fail_report()
        report["verdict"] = "pass"
        report["findings"] = []
        report["scenario_results"] = [
            {"scenario_id": "s1", "attempted": ["x"], "outcome": "pass"},
            {"scenario_id": "s2", "attempted": ["y"], "outcome": "pass"},
        ]
        wt = _make_worktree(report)
        ns, run = _ns(), _run(wt, _seeded_dicts())
        ns.worktree = wt
        with patch.object(l4_runner, "_emit_l4_findings") as m_emit, \
             patch.object(l4_runner, "_persist_l4_success") as m_ok, \
             patch.object(l4_runner, "_retry_l4_session") as m_retry:
            self._invoke(ns, run)
            m_emit.assert_not_called()
            m_ok.assert_called_once()
            m_retry.assert_not_called()

    def test_inconsistent_report_never_publishes(self):
        report = _fail_report()
        report["verdict"] = "pass"  # pass + findings = inconsistent
        wt = _make_worktree(report)
        ns, run = _ns(), _run(wt, _seeded_dicts())
        ns.worktree = wt
        with patch.object(l4_runner, "_emit_l4_findings") as m_emit, \
             patch.object(l4_runner, "_persist_l4_success") as m_ok, \
             patch.object(l4_runner, "_persist_l4_failure") as m_fail, \
             patch.object(l4_runner, "_retry_l4_session", return_value=False):
            self._invoke(ns, run)
            m_emit.assert_not_called()
            m_ok.assert_not_called()
            m_fail.assert_called_once()
            assert m_fail.call_args[0][2].startswith("structural:inconsistent")

    def test_below_floor_findings_never_publish(self):
        report = _fail_report()
        report["findings"] = [{
            "what": "Minor formatting nit",
            "where": ["src/login.tsx"],
            "why": "Cosmetic only",
            "severity": "low",
            "scenario_id": "s1",
        }]
        wt = _make_worktree(report)
        ns, run = _ns(), _run(wt, _seeded_dicts())
        ns.worktree = wt
        with patch.object(l4_runner, "_emit_l4_findings") as m_emit, \
             patch.object(l4_runner, "_persist_l4_success") as m_ok:
            self._invoke(ns, run)
            m_emit.assert_not_called()
            m_ok.assert_called_once()


class TestOnL4ObservedRetry:
    def _invoke(self, ns, run):
        return l4_runner._on_l4_observed(_FakeSession(ns, run), {
            "node_session_id": ns.id,
            "verdict": "done",
        })

    def test_first_structural_failure_retries_without_persisting(self):
        wt = tempfile.mkdtemp(prefix="l4_retry_")
        Path(wt, "l4_scratch").mkdir(parents=True)  # no report.json → missing_file
        ns, run = _ns(attempt=1), _run(wt, _seeded_dicts())
        ns.worktree = wt
        with patch.object(l4_runner, "_retry_l4_session", return_value=True) as m_retry, \
             patch.object(l4_runner, "_persist_l4_failure") as m_fail, \
             patch.object(l4_runner, "_persist_l4_on_parent") as m_parent, \
             patch.object(l4_runner, "_cleanup_l4_workspace") as m_clean:
            self._invoke(ns, run)
            m_retry.assert_called_once()
            m_fail.assert_not_called()
            m_parent.assert_not_called()
            m_clean.assert_not_called()

    def test_second_structural_failure_records_and_cleans(self):
        wt = tempfile.mkdtemp(prefix="l4_retry_")
        Path(wt, "l4_scratch").mkdir(parents=True)
        ns, run = _ns(attempt=2), _run(wt, _seeded_dicts())
        ns.worktree = wt
        with patch.object(l4_runner, "_retry_l4_session", return_value=False) as m_retry, \
             patch.object(l4_runner, "_persist_l4_failure") as m_fail, \
             patch.object(l4_runner, "_persist_l4_on_parent") as m_parent, \
             patch.object(l4_runner, "_cleanup_l4_workspace") as m_clean:
            self._invoke(ns, run)
            m_retry.assert_not_called()  # attempt already at bound
            m_fail.assert_called_once()
            m_parent.assert_called_once()
            m_clean.assert_called_once()

    def test_retry_sends_preamble_and_spawns_attempt_two(self):
        wt = tempfile.mkdtemp(prefix="l4_retry_")
        Path(wt, "l4_scratch").mkdir(parents=True)
        ns = _ns(attempt=1, conv="conv_1")
        run = _run(wt, _seeded_dicts())
        s = _FakeSession(ns, run)

        mock_aionui = MagicMock()
        with patch("backend.aionui.client.AionUiClient", return_value=mock_aionui), \
             patch.object(l4_runner, "_create_l4_node_session", return_value="ns_l4_retry2") as m_create, \
             patch.object(l4_runner, "_update_l4_node_session_conv") as m_conv, \
             patch.object(l4_runner, "_emit_l4_spawned") as m_spawn, \
             patch.object(l4_runner, "_cleanup_l4_workspace") as m_clean:
            retried = l4_runner._retry_l4_session(s, "db_url", ns, run.id, wt, "missing_file")

        assert retried is True
        msg = mock_aionui.send_message.call_args[0][1]
        assert "missing_file" in msg
        assert "failed structural validation" in msg
        m_create.assert_called_once_with("db_url", "l4_r1", wt, attempt=2)
        m_conv.assert_called_once_with("db_url", "ns_l4_retry2", "conv_1")
        m_spawn.assert_called_once()
        m_clean.assert_not_called()

    def test_retry_cancels_running_conversation_before_preamble(self):
        wt = tempfile.mkdtemp(prefix="l4_retry_")
        Path(wt, "l4_scratch").mkdir(parents=True)
        ns = _ns(attempt=1, conv="conv_1")
        run = _run(wt, _seeded_dicts())
        s = _FakeSession(ns, run)

        mock_aionui = MagicMock()
        with patch("backend.aionui.client.AionUiClient", return_value=mock_aionui), \
             patch.object(l4_runner, "_create_l4_node_session", return_value="ns_l4_retry2"), \
             patch.object(l4_runner, "_update_l4_node_session_conv"), \
             patch.object(l4_runner, "_emit_l4_spawned"):
            retried = l4_runner._retry_l4_session(s, "db_url", ns, run.id, wt, "missing_file")

        assert retried is True
        mock_aionui.cancel_conversation.assert_called_once_with("conv_1")
        call_names = [c[0] for c in mock_aionui.method_calls]
        assert call_names.index("cancel_conversation") < call_names.index("send_message")

    def test_retry_skipped_without_conversation(self):
        wt = tempfile.mkdtemp(prefix="l4_retry_")
        Path(wt, "l4_scratch").mkdir(parents=True)
        ns = _ns(attempt=1, conv=None)
        run = _run(wt, _seeded_dicts())
        retried = l4_runner._retry_l4_session(_FakeSession(ns, run), "db_url", ns, run.id, wt, "missing_file")
        assert retried is False


class TestL4RunHygiene:
    def test_create_l4_run_sets_merge_status_skipped(self):
        conn = MagicMock()
        with patch("psycopg.connect", return_value=conn):
            l4_runner._create_l4_run(
                "db_url", "l4_r1", "plan_x", "proj_x", "run_p_001",
                [Scenario(id="s1", as_a="user", wants="wants login", success_looks_like="sees dashboard")],
                "abc123",
            )
        cur = conn.__enter__.return_value.cursor.return_value.__enter__.return_value
        sql = cur.execute.call_args[0][0]
        assert "merge_status" in sql
        assert "'skipped'" in sql

    def test_node_session_attempt_parameterized(self):
        conn = MagicMock()
        with patch("psycopg.connect", return_value=conn):
            l4_runner._create_l4_node_session("db_url", "l4_r1", "/wt", attempt=2)
        cur = conn.__enter__.return_value.cursor.return_value.__enter__.return_value
        params = cur.execute.call_args[0][1]
        assert params[5] == 2
