"""Tests for File 01: evaluator schema + check generation.

[GATE 01]
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.evaluator.schema import Check, Judgment, NodeChecks
from backend.evaluator.generate import generate_checks


class TestCheckValidation:
    """1. Schema validation: deterministic vs rubric field requirements."""

    def test_deterministic_requires_check_cmd(self):
        Check(id="det-1", type="deterministic", criterion="tests pass",
              check_cmd="pytest -q")

    def test_deterministic_rejects_rubric_item(self):
        with pytest.raises(ValidationError, match="must not have 'rubric_item'"):
            Check(id="det-1", type="deterministic", criterion="tests pass",
                  check_cmd="pytest -q", rubric_item="Does it work?")

    def test_deterministic_fails_without_check_cmd(self):
        with pytest.raises(ValidationError, match="requires 'check_cmd'"):
            Check(id="det-1", type="deterministic", criterion="tests pass")

    def test_rubric_requires_rubric_item(self):
        Check(id="rubric-1", type="rubric", criterion="code quality",
              rubric_item="Is the code idiomatic?")

    def test_rubric_rejects_check_cmd(self):
        with pytest.raises(ValidationError, match="must not have 'check_cmd'"):
            Check(id="rubric-1", type="rubric", criterion="code quality",
                  rubric_item="Is it good?", check_cmd="pytest")

    def test_rubric_fails_without_rubric_item(self):
        with pytest.raises(ValidationError, match="requires 'rubric_item'"):
            Check(id="rubric-1", type="rubric", criterion="code quality")


class TestNodeChecks:
    """NodeChecks container with versioning."""

    def test_empty_checks_allowed(self):
        nc = NodeChecks(node_id="node-1")
        assert nc.checks == []
        assert nc.checks_version == 1

    def test_checks_with_items(self):
        c1 = Check(id="det-1", type="deterministic", criterion="tests pass",
                    check_cmd="pytest -q")
        c2 = Check(id="rubric-1", type="rubric", criterion="code quality",
                    rubric_item="Is it good?")
        nc = NodeChecks(node_id="node-1", checks=[c1, c2])
        assert len(nc.checks) == 2


class TestJudgment:
    """Judgment model for L2 judge output."""

    def test_minimal(self):
        j = Judgment(check_id="rubric-1", criteria_met=True,
                     explanation="All requirements covered")
        assert j.criteria_met is True

    def test_failed_judgment(self):
        j = Judgment(check_id="rubric-1", criteria_met=False,
                     explanation="Missing error handling")
        assert j.criteria_met is False


class TestGenerateChecks:
    """2. generate_checks produces a MIX (>=1 deterministic, >=1 rubric)."""

    def test_build_node_produces_mixed_checks(self):
        nc = generate_checks(
            node_id="node-1",
            task="Build CRUD API with tests",
            success_criterion="All CRUD endpoints work and tests pass",
            node_index=0,
            total_nodes=2,
        )
        assert any(c.type == "deterministic" and c.check_cmd for c in nc.checks)
        assert any(c.type == "rubric" and c.rubric_item for c in nc.checks)
        assert nc.node_id == "node-1"
        assert nc.checks_version == 1

    def test_test_node_uses_test_preset(self):
        nc = generate_checks(
            node_id="node-2",
            task="Write unit tests for the auth module",
            success_criterion="All auth tests pass with 80%+ coverage",
            node_index=1,
            total_nodes=2,
        )
        rubric_ids = {c.id for c in nc.checks if c.type == "rubric"}
        # generic_quality fallback when no members specified
        assert "meets_goal" in rubric_ids
        assert "complete" in rubric_ids
        assert any(c.type == "deterministic" for c in nc.checks)

    def test_review_node_uses_review_preset(self):
        nc = generate_checks(
            node_id="node-3",
            task="Review the implemented CRUD module",
            success_criterion="Review identifies all major issues",
            node_index=0,
            total_nodes=1,
            members=["opencode:reviewer"],
        )
        rubric_ids = {c.id for c in nc.checks if c.type == "rubric"}
        assert "ran_it" in rubric_ids
        assert "e2e_cycle" in rubric_ids
        assert "caught_issues" in rubric_ids

    def test_non_first_node_includes_regression_check(self):
        nc = generate_checks(
            node_id="node-2",
            task="Add caching layer",
            success_criterion="Cache reduces response time by 50%",
            node_index=1,
            total_nodes=3,
        )
        assert any(
            c.type == "deterministic" and "regression" in c.id
            for c in nc.checks
        )

    def test_first_node_has_no_regression_check(self):
        nc = generate_checks(
            node_id="node-1",
            task="Set up project structure",
            success_criterion="Project structure is created",
            node_index=0,
            total_nodes=1,
        )
        # Rubric "no regression" is fine for first nodes — only deterministic
        # regression checks should be absent on first nodes
        det_regression = [
            c for c in nc.checks
            if c.type == "deterministic" and "regression" in c.id
        ]
        assert len(det_regression) == 0

    def test_empty_criteria_still_gets_rubric_presets(self):
        nc = generate_checks(
            node_id="node-1",
            task="Random task",
            success_criterion="",
            node_index=0,
            total_nodes=1,
        )
        assert any(c.type == "rubric" for c in nc.checks)

    def test_deterministic_from_test_criterion(self):
        nc = generate_checks(
            node_id="node-1",
            task="Add tests",
            success_criterion="All pytest tests pass in CI",
            node_index=0,
            total_nodes=1,
        )
        assert any(
            c.type == "deterministic" and "tests" in c.id
            for c in nc.checks
        )

    def test_deterministic_from_endpoint_criterion(self):
        nc = generate_checks(
            node_id="node-1",
            task="Create health endpoint",
            success_criterion="The health endpoint returns 200 via http",
            node_index=0,
            total_nodes=1,
        )
        assert any(
            c.type == "deterministic" and "files" in c.id
            for c in nc.checks
        )

    def test_deterministic_from_code_criterion(self):
        nc = generate_checks(
            node_id="node-1",
            task="Implement user model",
            success_criterion="User class is implemented correctly",
            node_index=0,
            total_nodes=1,
        )
        assert any(
            c.type == "deterministic" and "syntax" in c.id
            for c in nc.checks
        )

    def test_chunknode_with_checks(self):
        """Verify ChunkNode accepts checks field."""
        from backend.planning.decomposed_spec import ChunkNode
        c1 = Check(id="det-1", type="deterministic", criterion="tests pass",
                    check_cmd="pytest -q")
        node = ChunkNode(
            id="node-1",
            members=["opencode:backend-executor"],
            checks=[c1],
        )
        assert len(node.checks) == 1
        assert node.checks[0].id == "det-1"

    def test_chunknode_checks_default_empty(self):
        """Verify ChunkNode works without checks field (backward compat)."""
        from backend.planning.decomposed_spec import ChunkNode
        node = ChunkNode(
            id="node-1",
            members=["opencode:backend-executor"],
        )
        assert node.checks == []

    def test_generated_checks_deduplicated(self):
        """Same check id doesn't appear twice."""
        nc = generate_checks(
            node_id="node-1",
            task="Build CRUD API with tests and code",
            success_criterion="All CRUD endpoints work, tests pass, and code is clean",
            node_index=0,
            total_nodes=1,
        )
        ids = [c.id for c in nc.checks]
        assert len(ids) == len(set(ids))
