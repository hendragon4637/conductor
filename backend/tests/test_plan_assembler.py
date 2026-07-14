"""Tests for File 01 / 03: deterministic .plan/ assembler + PlanDAG validation.

[GATE 01] / [GATE 03]
"""
from __future__ import annotations

import json
import os
import tempfile

import pytest
from pydantic import ValidationError

from contracts.plan_assembler import (
    PlanDAG,
    PlanNode,
    NodeTask,
    NodeSuccess,
    Member,
    assemble_plan,
    validate_assembled,
)


# ── Helpers ────────────────────────────────────────────────────────────────


def _make_worktree() -> str:
    """Create a temp worktree with .plan/ structure and return its path."""
    wt = tempfile.mkdtemp()
    os.makedirs(f"{wt}/.plan/nodes", exist_ok=True)
    return wt


def _write_index(wt: str, goal: str = "test goal", spec: str = "test spec",
                 quality: str = "test quality", nodes: list | None = None):
    idx = {
        "goal": goal,
        "spec": spec,
        "quality_intent": quality,
        "nodes": nodes or [
            {"id": "node-001", "file": "node-001.json", "depends_on": [],
             "description": "Test node"},
        ],
    }
    with open(f"{wt}/.plan/index.json", "w") as f:
        json.dump(idx, f)


def _write_node(wt: str, nid: str, fname: str, depends: list | None = None):
    node = {
        "id": nid,
        "capabilities": ["backend_api"],
        "members": [{"agent_config": "opencode:backend-executor", "backend": "opencode", "role": "executor"}],
        "depends_on": depends or [],
        "task": {"text": f"Do {nid}", "inputs": [], "deliverables": ["code"]},
        "success": {"text": f"{nid} done"},
    }
    with open(f"{wt}/.plan/nodes/{fname}", "w") as f:
        json.dump(node, f)


def _write_checks(wt: str, fname: str, checks: list | None = None):
    """Write a checks file for a node (required by assembler)."""
    os.makedirs(f"{wt}/.plan/checks", exist_ok=True)
    if checks is None:
        checks = [
            {"id": "run_md_present", "tier": "L1", "kind": "deterministic",
             "cmd": "test -f RUN.md", "criterion": "RUN.md exists"},
        ]
    with open(f"{wt}/.plan/checks/{fname}", "w") as f:
        json.dump(checks, f)


# ── PlanDAG Pydantic ─────────────────────────────────────────────────────


class TestPlanDAGValidation:

    def test_valid_dag(self):
        dag = PlanDAG(
            goal="test",
            spec="spec",
            quality_intent="quality",
            nodes=[
                PlanNode(
                    id="node-001",
                    capabilities=["api"],
                    members=[Member(agent_config="cfg1", backend="opencode", role="executor")],
                    depends_on=[],
                    task=NodeTask(text="do it", deliverables=["code"]),
                    success=NodeSuccess(text="done"),
                ),
            ],
        )
        assert dag.goal == "test"

    def test_duplicate_ids_raises(self):
        with pytest.raises(ValueError, match="duplicate node ids"):
            PlanDAG(
                goal="test", spec="s", quality_intent="q",
                nodes=[
                    PlanNode(id="a", members=[Member(agent_config="c", backend="o", role="e")],
                             task=NodeTask(text="t", deliverables=["d"]), success=NodeSuccess(text="ok")),
                    PlanNode(id="a", members=[Member(agent_config="c", backend="o", role="e")],
                             task=NodeTask(text="t", deliverables=["d"]), success=NodeSuccess(text="ok")),
                ],
            )

    def test_unresolved_dep_raises(self):
        with pytest.raises(ValueError, match="unresolved dep.*node-099"):
            PlanDAG(
                goal="test", spec="s", quality_intent="q",
                nodes=[
                    PlanNode(id="node-001", depends_on=["node-099"],
                             members=[Member(agent_config="c", backend="o", role="e")],
                             task=NodeTask(text="t", deliverables=["d"]), success=NodeSuccess(text="ok")),
                ],
            )

    def test_missing_deliverables_raises(self):
        with pytest.raises(ValueError, match="missing deliverables"):
            PlanDAG(
                goal="test", spec="s", quality_intent="q",
                nodes=[
                    PlanNode(id="node-001",
                             members=[Member(agent_config="c", backend="o", role="e")],
                             task=NodeTask(text="t", deliverables=[]), success=NodeSuccess(text="ok")),
                ],
            )

    def test_self_dep_raises(self):
        with pytest.raises(ValueError, match="depends_on itself"):
            PlanNode(id="node-001", depends_on=["node-001"],
                     members=[Member(agent_config="c", backend="o", role="e")],
                     task=NodeTask(text="t", deliverables=["d"]), success=NodeSuccess(text="ok"))

    def test_cycle_raises(self):
        with pytest.raises(ValueError, match="cycle"):
            PlanDAG(
                goal="test", spec="s", quality_intent="q",
                nodes=[
                    PlanNode(id="a", depends_on=["b"],
                             members=[Member(agent_config="c", backend="o", role="e")],
                             task=NodeTask(text="t", deliverables=["d"]), success=NodeSuccess(text="ok")),
                    PlanNode(id="b", depends_on=["a"],
                             members=[Member(agent_config="c", backend="o", role="e")],
                             task=NodeTask(text="t", deliverables=["d"]), success=NodeSuccess(text="ok")),
                ],
            )


# ── Deterministic assembler ──────────────────────────────────────────────


class TestAssemblePlan:

    def test_happy_path(self):
        wt = _make_worktree()
        _write_index(wt)
        _write_node(wt, "node-001", "node-001.json")
        _write_checks(wt, "node-001.json")
        dag_dict, errs = assemble_plan(wt)
        assert errs == []
        assert dag_dict is not None
        assert dag_dict["goal"] == "test goal"
        assert len(dag_dict["nodes"]) == 1
        assert dag_dict["nodes"][0]["id"] == "node-001"

    def test_missing_index(self):
        wt = _make_worktree()
        dag_dict, errs = assemble_plan(wt)
        assert dag_dict is None
        assert any("missing/malformed" in e for e in errs)

    def test_missing_index_field(self):
        wt = _make_worktree()
        with open(f"{wt}/.plan/index.json", "w") as f:
            json.dump({"goal": "test"}, f)
        dag_dict, errs = assemble_plan(wt)
        assert dag_dict is None
        assert any("missing required field" in e for e in errs)

    def test_orphan_node_file(self):
        wt = _make_worktree()
        _write_index(wt)
        _write_node(wt, "node-001", "node-001.json")
        # extra orphan file
        _write_node(wt, "node-002", "node-002.json")
        dag_dict, errs = assemble_plan(wt)
        assert dag_dict is None
        assert any("orphan" in e for e in errs)

    def test_missing_node_file(self):
        wt = _make_worktree()
    idx_nodes = [{"id": "node-001", "file": "node-001.json", "depends_on": [],
                  "description": "First node"},
                 {"id": "node-002", "file": "node-002.json", "depends_on": [],
                  "description": "Second node"}]
        _write_index(wt, nodes=idx_nodes)
        _write_node(wt, "node-001", "node-001.json")
        # node-002.json is missing
        dag_dict, errs = assemble_plan(wt)
        assert dag_dict is None
        assert any("missing file" in e for e in errs)

    def test_id_mismatch(self):
        wt = _make_worktree()
        _write_index(wt)
        # write node with wrong id
        node = {"id": "node-099", "capabilities": [], "members": [],
                "depends_on": [], "task": {"text": "x", "deliverables": ["d"]}, "success": {"text": "ok"}}
        with open(f"{wt}/.plan/nodes/node-001.json", "w") as f:
            json.dump(node, f)
        dag_dict, errs = assemble_plan(wt)
        assert dag_dict is None
        assert any("id" in e and "node-099" in e for e in errs)

    def test_malformed_node_json(self):
        wt = _make_worktree()
        _write_index(wt)
        with open(f"{wt}/.plan/nodes/node-001.json", "w") as f:
            f.write("not json")
        dag_dict, errs = assemble_plan(wt)
        assert dag_dict is None
        assert any("malformed" in e for e in errs)

    def test_malformed_index_json(self):
        wt = _make_worktree()
        with open(f"{wt}/.plan/index.json", "w") as f:
            f.write("not json")
        dag_dict, errs = assemble_plan(wt)
        assert dag_dict is None
        assert any("malformed" in e for e in errs)

    def test_multi_node_dag(self):
        wt = _make_worktree()
        idx_nodes = [
            {"id": "node-001", "file": "node-001.json", "depends_on": [],
             "description": "Node 1"},
            {"id": "node-002", "file": "node-002.json", "depends_on": ["node-001"],
             "description": "Node 2"},
            {"id": "node-003", "file": "node-003.json", "depends_on": ["node-002"],
             "description": "Node 3"},
        ]
        _write_index(wt, nodes=idx_nodes)
        _write_node(wt, "node-001", "node-001.json")
        _write_checks(wt, "node-001.json")
        _write_node(wt, "node-002", "node-002.json", depends=["node-001"])
        _write_checks(wt, "node-002.json")
        _write_node(wt, "node-003", "node-003.json", depends=["node-002"])
        _write_checks(wt, "node-003.json")
        dag_dict, errs = assemble_plan(wt)
        assert errs == []
        assert dag_dict is not None
        assert len(dag_dict["nodes"]) == 3


# ── validate_assembled ────────────────────────────────────────────────────


class TestValidateAssembled:

    def test_valid_happy_path(self):
        dag_dict = {
            "goal": "test",
            "spec": "spec",
            "quality_intent": "q",
            "nodes": [
                {
                    "id": "node-001",
                    "capabilities": ["api"],
                    "members": [{"agent_config": "opencode:backend-executor", "backend": "opencode", "role": "executor"}],
                    "depends_on": [],
                    "task": {"text": "do it", "inputs": [], "deliverables": ["code"]},
                    "success": {"text": "done"},
                },
            ],
        }
        roster = ["opencode:backend-executor"]
        dag, errs = validate_assembled(dag_dict, roster)
        assert errs == []
        assert dag is not None
        assert dag.goal == "test"

    def test_roster_mismatch(self):
        dag_dict = {
            "goal": "test", "spec": "s", "quality_intent": "q",
            "nodes": [
                {
                    "id": "node-001",
                    "members": [{"agent_config": "unknown-agent", "backend": "opencode", "role": "executor"}],
                    "depends_on": [],
                    "task": {"text": "t", "inputs": [], "deliverables": ["d"]},
                    "success": {"text": "ok"},
                },
            ],
        }
        roster = ["opencode:backend-executor"]
        dag, errs = validate_assembled(dag_dict, roster)
        assert dag is None
        assert any("not in roster" in e for e in errs)

    def test_invalid_pydantic_rejected(self):
        dag_dict = {
            "goal": "test",
            "spec": "s",
            "quality_intent": "q",
            "nodes": [
                {
                    "id": "node-001",
                    "members": "not-a-list",  # wrong type
                    "task": {"text": "t", "deliverables": ["d"]},
                    "success": {"text": "ok"},
                },
            ],
        }
        roster = ["opencode:backend-executor"]
        dag, errs = validate_assembled(dag_dict, roster)
        assert dag is None
        assert errs  # pydantic validation error captured


# ── Full pipeline: assemble → validate ────────────────────────────────────


class TestFullPipeline:

    def test_assemble_then_validate(self):
        wt = _make_worktree()
        _write_index(wt, goal="my goal", spec="my spec", quality="my quality")
        _write_node(wt, "node-001", "node-001.json")
        _write_checks(wt, "node-001.json")
        dag_dict, errs = assemble_plan(wt)
        assert errs == []
        dag, errs = validate_assembled(dag_dict, ["opencode:backend-executor"])
        assert errs == []
        assert dag is not None
        assert dag.goal == "my goal"
        assert len(dag.nodes) == 1
        assert dag.nodes[0].id == "node-001"

    def test_assemble_failure_blocks_validation(self):
        wt = _make_worktree()
        dag_dict, errs = assemble_plan(wt)
        assert dag_dict is None
        assert errs
        # validate_assembled can't proceed without dag_dict
        if dag_dict is None:
            pytest.skip("Assembler already failed")

    def test_multinode_assemble_and_validate(self):
        wt = _make_worktree()
        idx_nodes = [
            {"id": "a", "file": "a.json", "depends_on": []},
            {"id": "b", "file": "b.json", "depends_on": ["a"]},
        ]
        _write_index(wt, nodes=idx_nodes)
        _write_node(wt, "a", "a.json")
        _write_checks(wt, "a.json")
        _write_node(wt, "b", "b.json", depends=["a"])
        _write_checks(wt, "b.json")
        dag_dict, errs = assemble_plan(wt)
        assert errs == []
        dag, errs = validate_assembled(dag_dict, ["opencode:backend-executor"])
        assert errs == []
        assert len(dag.nodes) == 2
        assert dag.nodes[1].depends_on == ["a"]
