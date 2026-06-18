"""Tests for memory ↔ evaluator integration (File 08).

Covers read direction (memory-grounds-checks), write direction
(capture findings), and meta tier (ground_meta_evaluation).
Neo4j calls are mocked throughout.
"""
from __future__ import annotations

from unittest import mock

import pytest

from backend.evaluator.schema import Check


# ── Read direction: ground_checks_with_memory ────────────────────────────────


def test_ground_checks_with_memory_returns_list():
    """When memory search returns results, extra checks are produced."""
    mock_memories = [
        {"fact": "money is stored as integer cents, never float"},
        {"fact": "all API responses include a request_id header"},
    ]

    async def fake_search_memory(query, group, top_k=8):
        return mock_memories

    with mock.patch(
        "backend.memory.graphiti_client.search_memory",
        fake_search_memory,
    ):
        from backend.evaluator.memory_integration import ground_checks_with_memory
        checks = ground_checks_with_memory(
            task="build a CRUD API",
            project="finance-tracker",
            agent="executor",
        )

    assert len(checks) == 2
    for c in checks:
        assert c.type == "rubric"
        assert c.id.startswith("mem-rubric-")


def test_ground_checks_with_memory_empty_when_no_memories():
    """When memory returns nothing, returns empty list."""

    async def fake_search_memory(query, group, top_k=8):
        return []

    with mock.patch(
        "backend.memory.graphiti_client.search_memory",
        fake_search_memory,
    ):
        from backend.evaluator.memory_integration import ground_checks_with_memory
        checks = ground_checks_with_memory(
            task="build a CRUD API",
            project="finance-tracker",
            agent="executor",
        )

    assert checks == []


def test_ground_checks_with_memory_no_project():
    """Without a project, no memory search is attempted — returns empty."""
    from backend.evaluator.memory_integration import ground_checks_with_memory
    checks = ground_checks_with_memory(task="build a CRUD API")
    assert checks == []


def test_ground_checks_with_memory_deduplicates():
    """Duplicate memory texts produce only one check each."""
    mock_memories = [
        {"fact": "money is integer cents"},
        {"fact": "money is integer cents"},
    ]

    async def fake_search_memory(query, group, top_k=8):
        return mock_memories

    with mock.patch(
        "backend.memory.graphiti_client.search_memory",
        fake_search_memory,
    ):
        from backend.evaluator.memory_integration import ground_checks_with_memory
        checks = ground_checks_with_memory(
            task="build API", project="fp", agent="exec",
        )

    assert len(checks) == 1


def test_ground_checks_graceful_on_neo4j_error():
    """If Neo4j is unreachable, returns empty list (graceful degradation)."""

    async def fake_search_memory(query, group, top_k=8):
        raise ConnectionError("Neo4j unavailable")

    with mock.patch(
        "backend.memory.graphiti_client.search_memory",
        fake_search_memory,
    ):
        from backend.evaluator.memory_integration import ground_checks_with_memory
        checks = ground_checks_with_memory(
            task="build API", project="fp", agent="exec",
        )

    assert checks == []


# ── Write direction: capture_evaluator_findings ──────────────────────────────


def test_capture_writes_nothing_when_no_failures():
    """All-pass results should write zero memories."""
    from backend.evaluator.l1_checks import L1Result

    captured = {}

    async def fake_add_memory(text, group, source="", source_description="", ref_time=None):
        captured[text] = group

    with mock.patch(
        "backend.memory.graphiti_client.add_memory",
        fake_add_memory,
    ):
        from backend.evaluator.memory_integration import capture_evaluator_findings
        l1_ok = L1Result(passed=True, detail=[("det-1", True, "all good")])
        cnt = capture_evaluator_findings(
            node_id="node-1",
            l1_result=l1_ok,
            l2_result=None,
            project="fp",
            agent="exec",
            session_id="ses-1",
        )

    assert cnt == 0
    assert len(captured) == 0


def test_capture_writes_l1_failures():
    """Failing L1 checks should be written as memories."""
    from backend.evaluator.l1_checks import L1Result

    captured = {}

    async def fake_add_memory(text, group, source="", source_description="", ref_time=None):
        captured[text] = group

    with mock.patch(
        "backend.memory.graphiti_client.add_memory",
        fake_add_memory,
    ):
        from backend.evaluator.memory_integration import capture_evaluator_findings
        l1_fail = L1Result(
            passed=False,
            detail=[("det-tests", False, "pytest exit 1")],
        )
        cnt = capture_evaluator_findings(
            node_id="node-1",
            l1_result=l1_fail,
            l2_result=None,
            project="fp",
            agent="exec",
            session_id="ses-1",
        )

    assert cnt == 1
    assert any("det-tests" in k for k in captured)


def test_capture_writes_l2_failures():
    """Failing L2 rubric items should be written as memories."""
    from backend.evaluator.schema import Judgment
    from backend.evaluator.l2_judge import L2Result

    captured = {}

    async def fake_add_memory(text, group, source="", source_description="", ref_time=None):
        captured[text] = group

    l2_fail = L2Result(
        score=0.3,
        judgments=[
            Judgment(check_id="rubric-code", criteria_met=False, explanation="has anti-patterns"),
        ],
        rubric_count=1,
        items_met=0,
    )

    with mock.patch(
        "backend.memory.graphiti_client.add_memory",
        fake_add_memory,
    ):
        from backend.evaluator.memory_integration import capture_evaluator_findings
        cnt = capture_evaluator_findings(
            node_id="node-1",
            l1_result=None,
            l2_result=l2_fail,
            project="fp",
            agent="exec",
            session_id="ses-1",
        )

    assert cnt == 1
    assert any("rubric-code" in k for k in captured)


def test_capture_handles_missing_module_gracefully():
    """If memory module is absent, capture returns 0 without crashing."""
    from backend.evaluator.memory_integration import capture_evaluator_findings
    cnt = capture_evaluator_findings(
        node_id="node-1",
        l1_result=None,
        l2_result=None,
        project="fp",
        agent="exec",
    )
    assert cnt == 0


# ── generate_checks with extra_checks ────────────────────────────────────────


def test_generate_checks_includes_extra():
    """Extra checks from memory should appear in generated output."""
    from backend.evaluator.generate import generate_checks

    extra = [
        Check(
            id="mem-rubric-0",
            type="rubric",
            criterion="Recalled: money = integer cents",
            rubric_item="Does the output use integer cents for money?",
        ),
    ]

    result = generate_checks(
        node_id="node-1",
        task="build a CRUD expense API",
        success_criterion="all endpoints work",
        extra_checks=extra,
    )

    ids = [c.id for c in result.checks]
    assert "mem-rubric-0" in ids


def test_generate_checks_deduplicates_extra_with_preset():
    """If an extra check has the same id as a preset, it's deduplicated."""
    from backend.evaluator.generate import generate_checks

    extra = [
        Check(
            id="rubric-func-completeness",
            type="rubric",
            criterion="Duplicate",
            rubric_item="Is this duplicate?",
        ),
    ]

    result = generate_checks(
        node_id="node-1",
        task="build a CRUD expense API",
        success_criterion="all endpoints work",
        extra_checks=extra,
    )

    ids = [c.id for c in result.checks]
    assert ids.count("rubric-func-completeness") == 1


def test_generate_checks_extra_empty_by_default():
    """Without extra_checks, behavior is unchanged."""
    from backend.evaluator.generate import generate_checks

    result = generate_checks(
        node_id="node-1",
        task="write some tests",
        success_criterion="all tests pass",
    )

    assert len(result.checks) >= 1


# ── Meta tier: ground_meta_evaluation ────────────────────────────────────────


def test_meta_eval_no_decisions_file(tmp_path):
    """Without DECISIONS.md, returns empty list."""
    from backend.evaluator.memory_integration import _META_MEMORY_DIR, ground_meta_evaluation

    with mock.patch.object(
        type(_META_MEMORY_DIR), "parent",
        mock.PropertyMock(return_value=tmp_path),
    ):
        violations = ground_meta_evaluation("some plan")

    assert violations == []


def test_meta_eval_detects_golden_auto_label_violation(tmp_path):
    """Plan that mentions auto-labeling golden set is flagged."""
    decisions_md = tmp_path / "DECISIONS.md"
    decisions_md.write_text(
        "## 2026-06-12 — L3 meta-evaluation: golden set anchor + jury calibration\n"
        "Status: ACTIVE\n"
        "Decision: The golden set is written ONLY by human action.\n"
    )

    from backend.evaluator.memory_integration import _META_MEMORY_DIR, ground_meta_evaluation

    with mock.patch.object(
        type(_META_MEMORY_DIR), "parent",
        mock.PropertyMock(return_value=tmp_path),
    ):
        violations = ground_meta_evaluation(
            "auto-label golden set entries with LLM"
        )

    assert len(violations) >= 1
    assert any("golden" in v["invariant"].lower() for v in violations)


def test_meta_eval_skip_l1_violation(tmp_path):
    """Plan that proposes skipping L1 is flagged."""
    decisions_md = tmp_path / "DECISIONS.md"
    decisions_md.write_text(
        "## 2026-06-11 — Evaluator: L1 before L2\n"
        "Status: ACTIVE\n"
        "Decision: L1 deterministic checks run first.\n"
    )

    from backend.evaluator.memory_integration import _META_MEMORY_DIR, ground_meta_evaluation

    with mock.patch.object(
        type(_META_MEMORY_DIR), "parent",
        mock.PropertyMock(return_value=tmp_path),
    ):
        violations = ground_meta_evaluation(
            "skip L1 and go straight to rubric judge"
        )

    assert len(violations) >= 1


def test_meta_eval_clean_plan_no_violations(tmp_path):
    """A plan that doesn't violate any invariant returns empty."""
    decisions_md = tmp_path / "DECISIONS.md"
    decisions_md.write_text(
        "## 2026-06-11 — Evaluator: L1 before L2\n"
        "Status: ACTIVE\n"
        "Decision: L1 checks run first.\n"
    )

    from backend.evaluator.memory_integration import _META_MEMORY_DIR, ground_meta_evaluation

    with mock.patch.object(
        type(_META_MEMORY_DIR), "parent",
        mock.PropertyMock(return_value=tmp_path),
    ):
        violations = ground_meta_evaluation(
            "implement the expense tracker endpoints"
        )

    assert violations == []


def test_meta_eval_non_active_decisions_skipped(tmp_path):
    """Non-ACTIVE decisions are not checked."""
    decisions_md = tmp_path / "DECISIONS.md"
    decisions_md.write_text(
        "## 2026-06-10 — Old experiment\n"
        "Status: SUPERSEDED\n"
        "Decision: Frozen boundary not enforced.\n"
    )

    from backend.evaluator.memory_integration import _META_MEMORY_DIR, ground_meta_evaluation

    with mock.patch.object(
        type(_META_MEMORY_DIR), "parent",
        mock.PropertyMock(return_value=tmp_path),
    ):
        violations = ground_meta_evaluation("mutate frozen boundary")

    assert violations == []


# ── Anchor safety ────────────────────────────────────────────────────────────


def test_memory_integration_never_touches_golden_set():
    """Verify memory_integration has no reference to golden_set or add_golden."""
    import inspect
    from backend.evaluator import memory_integration

    source = inspect.getsource(memory_integration)
    assert "add_golden(" not in source, (
        "memory_integration must not write to the golden set"
    )
