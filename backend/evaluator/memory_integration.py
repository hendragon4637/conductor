"""Memory ↔ Evaluator integration — both read and write directions.

Read direction: memory grounds check/rubric generation by injecting
recalled conventions and past error patterns as extra checks.

Write direction: evaluator findings (failed L1 checks, low L2 rubric
scores, L3 drift) are captured as MemoryFact nodes in the product
knowledge graph (Neo4j), closing the learning loop.

Meta tier: Conductor-self evaluation retrieves locked architecture
decisions from DECISIONS.md to flag plan violations.

Critical boundary: memory NEVER reads from or writes to the L3 golden
set (frozen anchor). Promotion to global scope stays human-gated.
"""
from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Any

from backend.evaluator.schema import Check

logger = logging.getLogger(__name__)

# ── Read direction: ground checks with memory ────────────────────────────────


def _memory_to_check(text: str, idx: int) -> Check:
    """Convert a memory fact into a rubric Check item."""
    return Check(
        id=f"mem-rubric-{idx}",
        type="rubric",
        criterion=f"Recalled convention: {text[:120]}",
        rubric_item=(
            f"Does the output respect the following known convention: "
            f"'{text[:200]}'?"
        ),
        weight=1.0,
    )


def ground_checks_with_memory(
    task: str,
    project: str | None = None,
    agent: str | None = None,
) -> list[Check]:
    """Recall product memories relevant to a node and return extra checks.

    Searches product memory (Neo4j) for conventions matching the task
    and past error patterns for the agent, converting them into extra
    rubric checks that get injected into ``generate_checks()``.

    This is a sync wrapper — calls async ``search_memory`` via
    ``asyncio.run()``. If Neo4j is unreachable or no memories exist,
    returns an empty list (graceful degradation).

    Args:
        task: The node's task description (used as search query).
        project: Project scope for memory search.
        agent: Agent scope for past-error-pattern search.

    Returns:
        List of extra ``Check`` items derived from memory, or empty list.
    """
    return asyncio.run(_ground_checks_async(task, project, agent))


async def _ground_checks_async(
    task: str,
    project: str | None,
    agent: str | None,
) -> list[Check]:
    """Async implementation of ground_checks_with_memory."""
    try:
        from backend.memory.graphiti_client import search_memory
        from backend.memory.scopes import group_id
    except ImportError:
        logger.warning("Memory module not available — skipping grounding")
        return []

    extra: list[Check] = []
    seen_texts: set[str] = set()

    # 1. Search product-level conventions
    if project:
        product_scope = group_id("product", project)
        try:
            conventions = await search_memory(
                query=task,
                group=product_scope,
                top_k=5,
            )
            for mem in conventions:
                text = (mem.get("fact") or mem.get("text") or "").strip()
                if text and text not in seen_texts:
                    seen_texts.add(text)
                    extra.append(_memory_to_check(text, len(extra)))
        except Exception as exc:
            logger.debug("Memory search (product) failed: %s", exc)

    # 2. Search agent-level past error patterns
    if project and agent:
        agent_scope = group_id("product", project, agent)
        try:
            failures = await search_memory(
                query="recurring failure " + task,
                group=agent_scope,
                top_k=3,
            )
            for mem in failures:
                text = (mem.get("fact") or mem.get("text") or "").strip()
                if text and text not in seen_texts:
                    seen_texts.add(text)
                    extra.append(_memory_to_check(text, len(extra)))
        except Exception as exc:
            logger.debug("Memory search (agent) failed: %s", exc)

    return extra


# ── Write direction: capture evaluator findings as memories ─────────────────


def capture_evaluator_findings(
    node_id: str,
    l1_result: Any | None,
    l2_result: Any | None,
    project: str,
    agent: str,
    session_id: str | None = None,
) -> int:
    """Write evaluator findings back to product memory.

    Extracts failing L1 deterministic checks and low-scoring L2 rubric
    items from evaluator results and persists them as MemoryFact nodes
    at session scope. These can later be consolidated and promoted.

    Args:
        node_id: The node that was evaluated.
        l1_result: ``L1Result`` or ``None`` (if L1 was skipped).
        l2_result: ``L2Result`` or ``None`` (if L2 was skipped).
        project: Project name for scope.
        agent: Agent config name for scope.
        session_id: Optional session id for scoping writes at session level.

    Returns:
        Number of memories written.
    """
    return asyncio.run(
        _capture_async(node_id, l1_result, l2_result, project, agent, session_id),
    )


async def _capture_async(
    node_id: str,
    l1_result: Any | None,
    l2_result: Any | None,
    project: str,
    agent: str,
    session_id: str | None,
) -> int:
    """Async implementation of capture_evaluator_findings."""
    try:
        from backend.memory.graphiti_client import add_memory
        from backend.memory.scopes import group_id
    except ImportError:
        logger.warning("Memory module not available — skipping capture")
        return 0

    import datetime

    session_scope = group_id("product", project, agent, session_id)
    written = 0

    # L1 failures
    if l1_result is not None and hasattr(l1_result, "detail"):
        for check_id, ok, output in l1_result.detail:
            if not ok:
                text = (
                    f"L1 check '{check_id}' failed for node '{node_id}': "
                    f"{output[:200]}"
                )
                try:
                    await add_memory(
                        text=text,
                        group=session_scope,
                        source="evaluator",
                        source_description=f"L1 failure on node {node_id}",
                    )
                    written += 1
                except Exception as exc:
                    logger.debug("Failed to write L1 memory: %s", exc)

    # L2 low-scoring items
    if l2_result is not None:
        judgments = getattr(l2_result, "judgments", None) or []
        for j in judgments:
            if not j.criteria_met:
                text = (
                    f"L2 rubric '{j.check_id}' failed for node '{node_id}': "
                    f"{j.explanation[:200]}"
                )
                try:
                    await add_memory(
                        text=text,
                        group=session_scope,
                        source="evaluator",
                        source_description=f"L2 rubric failure on node {node_id}",
                    )
                    written += 1
                except Exception as exc:
                    logger.debug("Failed to write L2 memory: %s", exc)

    if written:
        logger.info("Captured %d evaluator findings as memories", written)
    return written


# ── Meta tier: ground conductor-self evaluation in meta memory ──────────────


_META_MEMORY_DIR = Path(__file__).resolve().parent.parent.parent
"""Repository root — where DECISIONS.md, CONVENTIONS.md etc. live."""


def _load_decisions() -> list[dict[str, str]]:
    """Load locked architecture decisions from DECISIONS.md.

    Returns a list of dicts with ``title``, ``status``, and ``decision`` keys,
    one per decision entry.
    """
    path = _META_MEMORY_DIR / "DECISIONS.md"
    if not path.exists():
        return []
    decisions: list[dict[str, str]] = []
    current: dict[str, str] = {}
    date_pattern = re.compile(r"^##\s+\d{4}-\d{2}-\d{2}\s+—\s+(.+)")
    status_pattern = re.compile(r"^Status:\s*(\S+)")

    with open(path) as f:
        for line in f:
            line = line.rstrip()
            m = date_pattern.match(line)
            if m:
                if current:
                    decisions.append(current)
                current = {"title": m.group(1).strip(), "status": "", "decision": ""}
                continue
            m = status_pattern.match(line)
            if m and current:
                current["status"] = m.group(1)
                continue
            if current and line.startswith("Decision:"):
                current["decision"] = line[len("Decision:"):].strip()

    if current:
        decisions.append(current)
    return decisions


def ground_meta_evaluation(
    plan_description: str,
) -> list[dict[str, str]]:
    """Check a plan description against locked architecture decisions.

    Reads DECISIONS.md for ACTIVE invariants and flags any that the
    plan description appears to violate.

    Args:
        plan_description: Free-text description of the plan being evaluated.

    Returns:
        List of dicts with keys ``invariant`` and ``note`` for each
        potential violation found. Empty list means no issues detected.
    """
    decisions = _load_decisions()
    violations: list[dict[str, str]] = []

    for dec in decisions:
        if dec.get("status", "").upper() != "ACTIVE":
            continue
        title = dec.get("title", "")
        decision = dec.get("decision", "")

        # Check for violation signals: plan description contradicts
        # the locked decision
        violation = _detect_violation(plan_description, title, decision)
        if violation:
            violations.append({
                "invariant": title,
                "note": violation,
            })

    return violations


def _detect_violation(
    plan_text: str,
    title: str,
    decision: str,
) -> str:
    """Heuristic violation detection — pattern-matches plan text against
    invariant keywords to flag potential contradictions.

    Returns empty string if no violation detected, or a description.
    """
    plan_lower = plan_text.lower()

    # Ratchet / frozen-boundary: plan wants to mutate a frozen field
    if "move to" in plan_lower and any(kw in decision.lower() for kw in
                                       ("frozen", "never mutate", "must not")):
        return f"Plan may violate the '{title}' invariant: {decision[:200]}"

    # Golden set / anchor: plan wants to auto-label
    if ("auto" in plan_lower and "golden" in plan_lower):
        return (
            f"Plan mentions auto-labeling golden set, but '{title}' "
            f"requires human-only writes to the anchor"
        )

    # Evaluator order: plan skips L1 before L2
    if ("skip l1" in plan_lower or "bypass l1" in plan_lower):
        return (
            f"Plan proposes skipping L1, but '{title}' requires "
            f"deterministic checks before LLM-based evaluation"
        )

    return ""
