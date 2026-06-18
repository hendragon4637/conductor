"""GATE — Backend Modes v5 capstone tests.

Pass conditions:
1. Backend registry taxonomy is complete and consistent
2. BackendClass enum and is_self_orchestrating align
3. select_run returns correct execution plan per backend
4. Quality intent parsing produces provenance-tagged checks
5. generate_checks with quality_intent is backward-compatible
6. HermesClient constructs correct API URLs and handles errors
7. OpenCode config writer produces valid per-worktree config
8. Check schema accepts new provenance fields
9. MCP tool signatures accept dual-input params
10. grouped_backends produces correct UI groupings
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, "/opt/aipc/conductor")

CLASS_A = frozenset({"hermes", "opencode_omo", "openclaw", "paperclip"})
CLASS_B = frozenset({"opencode", "claude_code", "codex", "gemini", "aionui"})


# ══════════════════════════════════════════════════════════════════════════════
# 1. Backend registry taxonomy
# ══════════════════════════════════════════════════════════════════════════════

class TestBackendRegistry:
    """Backend taxonomy: classes, lookups, execution plans."""

    def test_all_backends_listed(self):
        from backend.backends.registry import BACKENDS
        expected = CLASS_A | CLASS_B
        assert set(BACKENDS.keys()) == expected

    def test_each_backend_has_required_fields(self):
        from backend.backends.registry import BACKENDS
        for name, meta in BACKENDS.items():
            assert "class" in meta, f"{name} missing 'class'"
            assert "label" in meta, f"{name} missing 'label'"
            assert meta["class"] in ("a", "b"), f"{name}: bad class {meta['class']!r}"

    def test_class_a_backends_are_self_orchestrating(self):
        from backend.backends.registry import is_self_orchestrating, BACKENDS
        for name in BACKENDS:
            expected = name in CLASS_A
            assert is_self_orchestrating(name) is expected, (
                f"{name}: expected self_orchestrating={expected}"
            )

    def test_class_b_backends_are_not_self_orchestrating(self):
        from backend.backends.registry import is_self_orchestrating
        for name in CLASS_B:
            assert is_self_orchestrating(name) is False

    def test_is_self_orchestrating_unknown_raises(self):
        from backend.backends.registry import is_self_orchestrating
        with pytest.raises(KeyError):
            is_self_orchestrating("nonexistent")

    def test_get_backend_raises_for_unknown(self):
        from backend.backends.registry import get_backend
        with pytest.raises(KeyError, match="nonexistent"):
            get_backend("nonexistent")


# ══════════════════════════════════════════════════════════════════════════════
# 2. select_run — execution plan per backend
# ══════════════════════════════════════════════════════════════════════════════

class TestSelectRun:
    """select_run returns correct execution plan per backend."""

    def test_hermes_no_orchestrator(self):
        from backend.backends.registry import select_run
        plan = select_run("hermes")
        assert plan["orchestrator"] is False
        assert plan["members"] == ["hermes"]

    def test_opencode_has_orchestrator(self):
        from backend.backends.registry import select_run
        plan = select_run("opencode")
        assert plan["orchestrator"] is True
        assert "opencode" in plan["members"]

    def test_aionui_is_team(self):
        from backend.backends.registry import select_run
        plan = select_run("aionui")
        assert plan["orchestrator"] is True
        assert plan["mode"] == "aionui_team"

    def test_select_run_unknown_raises(self):
        from backend.backends.registry import select_run
        with pytest.raises(KeyError):
            select_run("nonexistent")

    def test_select_run_class_a_no_orchestrator(self):
        from backend.backends.registry import select_run
        for name in CLASS_A:
            plan = select_run(name)
            assert plan["orchestrator"] is False, (
                f"{name}: class-a but has orchestrator"
            )
            assert plan["mode"] == "direct_or_member"

    def test_select_run_class_b_has_orchestrator(self):
        from backend.backends.registry import select_run
        for name in CLASS_B:
            plan = select_run(name)
            assert plan["orchestrator"] is True, (
                f"{name}: class-b but no orchestrator"
            )

    def test_select_run_with_custom_members(self):
        from backend.backends.registry import select_run
        plan = select_run("opencode", members=["opencode:exec", "opencode:review"])
        assert plan["orchestrator"] is True
        assert len(plan["members"]) == 2

    def test_run_mode_for(self):
        from backend.backends.registry import run_mode_for
        assert run_mode_for("hermes") == "direct_or_member"
        assert run_mode_for("opencode") == "aionui_member"
        assert run_mode_for("aionui") == "aionui_team"

    def test_is_single_agent(self):
        from backend.backends.registry import is_single_agent
        assert is_single_agent("opencode") is True
        assert is_single_agent("hermes") is False


# ══════════════════════════════════════════════════════════════════════════════
# 3. grouped_backends — UI dropdown grouping
# ══════════════════════════════════════════════════════════════════════════════

class TestGroupedBackends:
    """grouped_backends produces correct UI groupings."""

    def test_grouped_backends_returns_three_groups(self):
        from backend.backends.registry import grouped_backends
        groups = grouped_backends()
        assert "Self-orchestrating (a) · no orchestrator" in groups
        assert "Single-agent (b) · orchestrator + members" in groups
        assert "Team" in groups

    def test_group_a_contains_self_orchestrating(self):
        from backend.backends.registry import grouped_backends
        groups = grouped_backends()
        a_keys = {e["key"] for e in groups["Self-orchestrating (a) · no orchestrator"]}
        assert a_keys == CLASS_A

    def test_team_group_contains_aionui(self):
        from backend.backends.registry import grouped_backends
        groups = grouped_backends()
        team_keys = {e["key"] for e in groups["Team"]}
        assert "aionui" in team_keys


# ══════════════════════════════════════════════════════════════════════════════
# 4. Evaluator schema — provenance fields
# ══════════════════════════════════════════════════════════════════════════════

class TestCheckProvenanceSchema:
    """Check model accepts new provenance fields."""

    def test_check_default_provenance_is_preset(self):
        from backend.evaluator.schema import Check
        c = Check(id="det-1", type="deterministic", criterion="tests pass",
                  check_cmd="pytest -q")
        assert c.provenance == "preset"
        assert c.source_hint is None

    def test_check_human_intent_provenance(self):
        from backend.evaluator.schema import Check
        c = Check(id="det-1", type="deterministic", criterion="tests pass",
                  check_cmd="pytest -q",
                  provenance="human_intent",
                  source_hint="from quality_intent: money must be integer cents")
        assert c.provenance == "human_intent"
        assert c.source_hint == "from quality_intent: money must be integer cents"

    def test_check_memory_provenance(self):
        from backend.evaluator.schema import Check
        c = Check(id="mem-rubric-0", type="rubric",
                  criterion="Recalled: money = integer cents",
                  rubric_item="Does the output use integer cents?",
                  provenance="memory")
        assert c.provenance == "memory"

    def test_check_rejects_invalid_provenance(self):
        from backend.evaluator.schema import Check
        import pydantic
        with pytest.raises(pydantic.ValidationError):
            Check(id="bad-1", type="rubric", criterion="bad",
                  rubric_item="bad?", provenance="invalid_source")

    def test_node_checks_preserves_provenance(self):
        from backend.evaluator.schema import Check, NodeChecks
        c1 = Check(id="det-1", type="deterministic", criterion="tests pass",
                    check_cmd="pytest -q", provenance="human_intent")
        c2 = Check(id="rubric-1", type="rubric", criterion="quality",
                    rubric_item="Is it good?", provenance="memory")
        nc = NodeChecks(node_id="node-1", checks=[c1, c2])
        provenances = {c.provenance for c in nc.checks}
        assert provenances == {"human_intent", "memory"}


# ══════════════════════════════════════════════════════════════════════════════
# 5. Quality intent parsing
# ══════════════════════════════════════════════════════════════════════════════

class TestQualityIntentParsing:
    """_generate_from_quality_intent produces provenance-tagged checks."""

    def test_generates_checks_from_quality_intent(self):
        from backend.evaluator.generate import _generate_from_quality_intent
        checks = _generate_from_quality_intent(
            "money must be integer cents, deletes need confirmation"
        )
        assert len(checks) >= 1
        for c in checks:
            assert c.provenance == "human_intent"
            assert c.source_hint is not None

    def test_returns_check_objects(self):
        from backend.evaluator.generate import _generate_from_quality_intent
        from backend.evaluator.schema import Check
        checks = _generate_from_quality_intent("API must return 200")
        assert all(isinstance(c, Check) for c in checks)

    def test_empty_quality_intent_returns_empty(self):
        from backend.evaluator.generate import _generate_from_quality_intent
        checks = _generate_from_quality_intent("")
        assert checks == []

    def test_none_quality_intent_returns_empty(self):
        from backend.evaluator.generate import _generate_from_quality_intent
        checks = _generate_from_quality_intent(None)
        assert checks == []

    def test_quality_intent_with_enforce_keyword(self):
        from backend.evaluator.generate import _generate_from_quality_intent
        checks = _generate_from_quality_intent(
            "enforce that all money fields are integer cents"
        )
        assert len(checks) >= 1

    def test_quality_intent_mixed_clauses(self):
        from backend.evaluator.generate import _generate_from_quality_intent
        checks = _generate_from_quality_intent(
            "money must be integer cents; reject floats; deletes require confirm"
        )
        assert len(checks) >= 1
        source_hints = [c.source_hint for c in checks]
        assert len(set(source_hints)) >= 1


# ══════════════════════════════════════════════════════════════════════════════
# 6. generate_checks — quality_intent integration
# ══════════════════════════════════════════════════════════════════════════════

class TestGenerateChecksWithQualityIntent:
    """generate_checks with quality_intent produces provenance-tagged checks."""

    def test_backward_compat_no_quality_intent(self):
        from backend.evaluator.generate import generate_checks
        result = generate_checks(
            node_id="node-1",
            task="Build CRUD API",
            success_criterion="API works",
        )
        assert len(result.checks) >= 1
        assert all(c.provenance == "preset" for c in result.checks)

    def test_quality_intent_appears_in_checks(self):
        from backend.evaluator.generate import generate_checks
        result = generate_checks(
            node_id="node-1",
            task="Build CRUD API",
            success_criterion="API works",
            quality_intent="money must be integer cents",
        )
        human_intent_checks = [c for c in result.checks if c.provenance == "human_intent"]
        assert len(human_intent_checks) >= 1
        assert any("money" in c.criterion for c in human_intent_checks)

    def test_quality_intent_with_extra_checks(self):
        from backend.evaluator.schema import Check
        from backend.evaluator.generate import generate_checks
        extra = [
            Check(id="mem-rubric-0", type="rubric",
                  criterion="Recalled: use integer cents",
                  rubric_item="Uses integer cents?",
                  provenance="memory"),
        ]
        result = generate_checks(
            node_id="node-1",
            task="Build CRUD API",
            success_criterion="API works",
            extra_checks=extra,
            quality_intent="deletes need confirmation",
        )
        provenances = {c.provenance for c in result.checks}
        assert "memory" in provenances
        assert "human_intent" in provenances
        assert "preset" in provenances

    def test_quality_intent_checks_deduplicated(self):
        from backend.evaluator.generate import generate_checks
        result = generate_checks(
            node_id="node-1",
            task="Build CRUD API",
            success_criterion="API works",
            quality_intent="money must be integer cents, money must be integer cents",
        )
        human_intent_ids = [
            c.id for c in result.checks if c.provenance == "human_intent"
        ]
        assert len(human_intent_ids) == len(set(human_intent_ids))

    def test_empty_quality_intent_in_generate_checks(self):
        from backend.evaluator.generate import generate_checks
        result = generate_checks(
            node_id="node-1",
            task="Build CRUD API",
            success_criterion="API works",
            quality_intent="",
        )
        human_intent = [c for c in result.checks if c.provenance == "human_intent"]
        assert len(human_intent) == 0


# ══════════════════════════════════════════════════════════════════════════════
# 7. Hermes adapter — HTTP client
# ══════════════════════════════════════════════════════════════════════════════

class TestHermesAdapter:
    """HermesClient constructs correct URLs and handles errors."""

    def test_default_host(self):
        from backend.hermes_adapter import HermesClient
        c = HermesClient()
        assert c.host == "http://localhost:8642"

    def test_custom_host(self):
        from backend.hermes_adapter import HermesClient
        c = HermesClient(host="http://hermes:8642")
        assert c.host == "http://hermes:8642"

    def test_env_host(self):
        from backend.hermes_adapter import HermesClient
        with mock.patch.dict(os.environ, {"HERMES_HOST": "http://hermes-svc:8642"}):
            c = HermesClient()
            assert c.host == "http://hermes-svc:8642"

    def test_create_run_url(self):
        from backend.hermes_adapter import HermesClient
        c = HermesClient(host="http://hermes:8642")
        with mock.patch.object(c, "_request") as mock_req:
            mock_req.return_value = {"id": "run-abc"}
            c.create_run("build feature X", "/tmp/worktree")
            mock_req.assert_called_once_with(
                "POST", "/v1/runs",
                {"goal": "build feature X", "workspace": "/tmp/worktree"}
            )

    def test_get_run_status_url(self):
        from backend.hermes_adapter import HermesClient
        c = HermesClient(host="http://hermes:8642")
        with mock.patch.object(c, "_request") as mock_req:
            mock_req.return_value = {"status": "completed"}
            c.get_run_status("run-123")
            mock_req.assert_called_once_with("GET", "/v1/runs/run-123")

    def test_stop_run_url(self):
        from backend.hermes_adapter import HermesClient
        c = HermesClient(host="http://hermes:8642")
        with mock.patch.object(c, "_request") as mock_req:
            c.stop_run("run-123")
            mock_req.assert_called_once_with("DELETE", "/v1/runs/run-123")

    def test_create_run_returns_run_id(self):
        from backend.hermes_adapter import HermesClient
        c = HermesClient(host="http://hermes:8642")
        with mock.patch.object(c, "_request") as mock_req:
            mock_req.return_value = {"id": "run-abc"}
            result = c.create_run("build X", "/tmp/wt")
            assert result["id"] == "run-abc"

    def test_get_run_status_returns_status(self):
        from backend.hermes_adapter import HermesClient
        c = HermesClient(host="http://hermes:8642")
        with mock.patch.object(c, "_request") as mock_req:
            mock_req.return_value = {"status": "running", "id": "run-abc"}
            result = c.get_run_status("run-abc")
            assert result["status"] == "running"

    def test_create_run_failure_raises_runtime_error(self):
        from backend.hermes_adapter import HermesClient
        c = HermesClient(host="http://hermes:8642")
        with mock.patch.object(c, "_request") as mock_req:
            mock_req.side_effect = RuntimeError("Hermes API POST /v1/runs failed")
            with pytest.raises(RuntimeError, match="Hermes API"):
                c.create_run("build X", "/tmp/wt")

    def test_connection_error_raises_runtime_error(self):
        from backend.hermes_adapter import HermesClient
        c = HermesClient(host="http://hermes:8642")
        with mock.patch.object(c, "_request") as mock_req:
            mock_req.side_effect = RuntimeError("Hermes API POST /v1/runs connection failed")
            with pytest.raises(RuntimeError, match="connection failed"):
                c.create_run("build X", "/tmp/wt")

    def test_request_includes_api_key_from_env(self):
        from backend.hermes_adapter import HermesClient
        from urllib.request import Request
        import urllib.request
        c = HermesClient(host="http://hermes:8642")
        with mock.patch.dict(os.environ, {"HERMES_API_KEY": "test-key"}):
            with mock.patch("urllib.request.urlopen") as mock_urlopen:
                mock_resp = mock.MagicMock()
                mock_resp.__enter__.return_value.read.return_value = json.dumps({"id": "r"}).encode()
                mock_urlopen.return_value = mock_resp
                c.create_run("goal", "/wt")
                req = mock_urlopen.call_args[0][0]
                assert req.headers.get("Authorization") == "Bearer test-key"

    def test_request_without_api_key(self):
        from backend.hermes_adapter import HermesClient
        import urllib.request
        c = HermesClient(host="http://hermes:8642")
        with mock.patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = mock.MagicMock()
            mock_resp.__enter__.return_value.read.return_value = json.dumps({"id": "r"}).encode()
            mock_urlopen.return_value = mock_resp
            with mock.patch.dict(os.environ, {}, clear=True):
                if "HERMES_API_KEY" in os.environ:
                    del os.environ["HERMES_API_KEY"]
                c.create_run("goal", "/wt")
                req = mock_urlopen.call_args[0][0]
                assert "Authorization" not in req.headers


# ══════════════════════════════════════════════════════════════════════════════
# 8. OpenCode per-worktree config writer
# ══════════════════════════════════════════════════════════════════════════════

class TestOpenCodeConfigWriter:
    """write_worktree_config and spawn_env_for produce valid configs."""

    def test_write_worktree_config_creates_opencode_json(self):
        from backend.backends.opencode_config import write_worktree_config
        with tempfile.TemporaryDirectory() as tmp:
            result = write_worktree_config(worktree=tmp, model="claude-sonnet-4-6")
            config_file = Path(tmp) / "opencode.json"
            assert config_file.exists(), f"Expected {config_file}"
            data = json.loads(config_file.read_text())
            assert data.get("model") == "claude-sonnet-4-6"

    def test_write_worktree_config_writes_brief_file(self):
        from backend.backends.opencode_config import write_worktree_config
        with tempfile.TemporaryDirectory() as tmp:
            write_worktree_config(
                worktree=tmp,
                model="claude-sonnet-4-6",
                appended_prompt="You are an executor agent for this node",
            )
            brief_path = Path(tmp) / ".conductor" / "NODE_BRIEF.md"
            assert brief_path.exists()
            assert brief_path.read_text() == "You are an executor agent for this node"

    def test_write_worktree_config_opencode_omo_writes_omo_config(self):
        from backend.backends.opencode_config import write_worktree_config
        with tempfile.TemporaryDirectory() as tmp:
            result = write_worktree_config(
                worktree=tmp,
                model="openrouter/deepseek",
                agent_type="opencode_omo",
            )
            omo_path = Path(tmp) / "oh-my-openagent.jsonc"
            assert omo_path.exists()
            assert "omo_config_path" in result

    def test_write_worktree_config_plain_opencode_no_omo(self):
        from backend.backends.opencode_config import write_worktree_config
        with tempfile.TemporaryDirectory() as tmp:
            result = write_worktree_config(
                worktree=tmp,
                model="claude-sonnet-4-6",
                agent_type="opencode",
            )
            omo_path = Path(tmp) / "oh-my-openagent.jsonc"
            assert "omo_config_path" not in result
            assert not omo_path.exists()

    def test_spawn_env_for_opencode(self):
        from backend.backends.opencode_config import spawn_env_for
        env = spawn_env_for("opencode")
        assert env.get("OPENCODE_OMO") == "false"

    def test_spawn_env_for_opencode_omo(self):
        from backend.backends.opencode_config import spawn_env_for
        env = spawn_env_for("opencode_omo")
        assert env.get("OPENCODE_OMO") == "true"

    def test_spawn_env_for_unknown_returns_empty(self):
        from backend.backends.opencode_config import spawn_env_for
        env = spawn_env_for("hermes")
        assert env == {}

    def test_cleanup_worktree_config_removes_files(self):
        from backend.backends.opencode_config import write_worktree_config, cleanup_worktree_config
        with tempfile.TemporaryDirectory() as tmp:
            write_worktree_config(worktree=tmp, model="test")
            assert (Path(tmp) / "opencode.json").exists()
            cleanup_worktree_config(tmp)
            assert not (Path(tmp) / "opencode.json").exists()


# ══════════════════════════════════════════════════════════════════════════════
# 9. MCP tool signatures — dual-input params
# ══════════════════════════════════════════════════════════════════════════════

class TestMCPToolSignatures:
    """MCP create_plan and refine_plan accept dual-input params."""

    def test_create_plan_accepts_spec_and_quality_intent(self):
        from backend.mcp.tools import handle_create_plan
        import inspect
        sig = inspect.signature(handle_create_plan)
        assert "spec" in sig.parameters
        assert "quality_intent" in sig.parameters
        assert sig.parameters["spec"].default is None
        assert sig.parameters["quality_intent"].default is None

    def test_refine_plan_accepts_quality_intent(self):
        from backend.mcp.tools import handle_refine_plan
        import inspect
        sig = inspect.signature(handle_refine_plan)
        assert "quality_intent" in sig.parameters
        assert sig.parameters["quality_intent"].default is None

    def test_mcp_server_create_plan_includes_spec(self):
        from backend.mcp.server import create_mcp_app
        mcp = create_mcp_app()
        tools = mcp._tool_manager.list_tools()
        tool_map = {t.name: t for t in tools}
        create = tool_map["conductor-create_plan"]
        props = create.parameters.get("properties", {})
        assert "spec" in props
        assert "quality_intent" in props

    def test_mcp_server_refine_plan_includes_quality_intent(self):
        from backend.mcp.server import create_mcp_app
        mcp = create_mcp_app()
        tools = mcp._tool_manager.list_tools()
        tool_map = {t.name: t for t in tools}
        refine = tool_map["conductor-refine_plan"]
        props = refine.parameters.get("properties", {})
        assert "quality_intent" in props

    def test_mcp_tools_count_unchanged(self):
        from backend.mcp.server import create_mcp_app
        mcp = create_mcp_app()
        tools = mcp._tool_manager.list_tools()
        assert len(tools) == 5


# ══════════════════════════════════════════════════════════════════════════════
# 10. End-to-end integration scenarios
# ══════════════════════════════════════════════════════════════════════════════

class TestEndToEndScenarios:
    """Cross-module integration scenarios."""

    def test_registry_feeds_hermes_adapter(self):
        from backend.backends.registry import select_run
        from backend.hermes_adapter import HermesClient
        plan = select_run("hermes")
        assert plan["orchestrator"] is False
        c = HermesClient()
        assert c.host == "http://localhost:8642"

    def test_registry_opencode_omo(self):
        from backend.backends.registry import select_run
        from backend.backends.opencode_config import spawn_env_for
        plan = select_run("opencode_omo")
        assert plan["orchestrator"] is False
        env = spawn_env_for("opencode_omo")
        assert env.get("OPENCODE_OMO") == "true"

    def test_is_self_orchestrating_consistency(self):
        from backend.backends.registry import BACKENDS, is_self_orchestrating
        for name, meta in BACKENDS.items():
            expected = name in CLASS_A
            assert is_self_orchestrating(name) is expected, (
                f"{name}: class={meta['class']} but is_self_orch={not expected}"
            )

    def test_quality_intent_and_memory_coexist(self):
        from backend.evaluator.schema import Check
        from backend.evaluator.generate import generate_checks
        extra = [
            Check(id="mem-1", type="rubric", criterion="memory fact",
                  rubric_item="Does it?", provenance="memory"),
        ]
        result = generate_checks(
            node_id="n1", task="task", success_criterion="works",
            extra_checks=extra, quality_intent="money must be integer cents",
        )
        provenances = {c.provenance for c in result.checks}
        assert provenances == {"preset", "memory", "human_intent"}

    def test_hermes_adapter_passes_goal_and_worktree(self):
        from backend.hermes_adapter import HermesClient
        c = HermesClient(host="http://hermes:8642")
        with mock.patch.object(c, "_request") as mock_req:
            c.create_run("build feature X", "/workspace/node-1")
            args, kwargs = mock_req.call_args
            assert args[0] == "POST"
            assert args[1] == "/v1/runs"
            body = args[2]
            assert body["goal"] == "build feature X"
            assert body["workspace"] == "/workspace/node-1"

    def test_backends_registry_is_single_source_of_truth(self):
        from backend.backends.registry import BACKENDS
        from backend.hermes_adapter import HermesClient
        from backend.backends.opencode_config import spawn_env_for
        for name in BACKENDS:
            assert isinstance(name, str)
        HermesClient()
        opencode_env = spawn_env_for("opencode")
        assert isinstance(opencode_env, dict)
