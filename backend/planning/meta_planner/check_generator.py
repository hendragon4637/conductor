"""File 02b — Check Generator (separate LLM call #2b).

After the decomposer proposes the DAG, this module attaches per-node
evaluation checks — selecting L1 (deterministic) and L2 (rubric) patterns
from retrievable rubric presets, grounded by quality_intent + memory.

Keeping this as a SEPARATE call from the decomposer prevents the proposer
from authoring its own lenient passing bar (correlated-error problem).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel, Field

from backend.evaluator.rubrics import load_all_rubrics
from backend.evaluator.schema import Check, NodeChecks
from backend.planning.meta_planner.decomposer import PlanDAG
from backend.planning.meta_planner.llm import call_llm_structured

logger = logging.getLogger(__name__)


class PerNodeChecks(BaseModel):
    """Checks attached to a single node by the check-generator."""
    node_id: str
    checks: list[Check] = Field(
        default_factory=list,
        description="Generated checks (L1 deterministic + L2 rubric)",
    )


class AllChecks(BaseModel):
    """All per-node check lists for the DAG."""
    nodes: list[PerNodeChecks] = Field(
        min_length=1,
        description="One entry per node, in the same order as the DAG",
    )


CHECKGEN_PROMPT = """\
You are a check-generation engine. Given a plan DAG, a quality intent string,
retrievable rubric presets, and optional memory context, produce evaluation
checks for each node.

Guidelines:
  - Each node gets a mix of L1 (deterministic/shell-verifiable) and L2
    (rubric/quality-question) checks.
  - L1 checks are shell commands run in the worktree (exit 0 = pass).
    They test concrete things: file existence, syntax checks, test execution.
    L1 checks MUST NOT contain runtime signals (curl, localhost, uvicorn,
    http://, :8000, :3000, health endpoints) — those belong to L4.
  - L2 checks are rubric items: yes/no quality questions the L2 judge
    evaluates. They test correctness, completeness, error handling, etc.
  - Select the MOST relevant checks from the available rubric presets.
    Don't add every possible check — only what matters for the node's task.
  - Ground checks in the quality_intent when possible.
  - Weight matters: 2.0 = critical, 1.0 = normal, 0.5 = nice-to-have.
  - provenance must be one of: "preset", "human_intent", "memory"

Available rubric presets:
{rubrics}

Quality intent:
{quality_intent}

Memory context:
{memory}

Plan DAG:
{dag}

Now produce the AllChecks JSON — one PerNodeChecks entry per node, with
matching node_id values."""


def generate_checks(
    dag: PlanDAG,
    quality_intent: str = "",
    memory: str = "",
) -> AllChecks:
    """Generate evaluation checks for all nodes in a plan DAG.

    This is a SEPARATE LLM call from the decomposer. The check-generator
    sees the proposed DAG and selects/rubs checks from the available
    rubric presets, grounded in quality_intent and memory.

    Args:
        dag: The validated PlanDAG from the decomposer.
        quality_intent: Free-text quality guidance from the goal formulator.
        memory: Optional recalled memory context.

    Returns:
        An ``AllChecks`` with one ``PerNodeChecks`` per node.

    Raises:
        RuntimeError: If the LLM call fails after retries.
    """
    rubrics = load_all_rubrics()
    rubrics_str = json.dumps(rubrics, indent=2) if rubrics else "(no rubrics loaded)"

    dag_summary = _dag_for_prompt(dag)

    prompt = CHECKGEN_PROMPT.format(
        rubrics=rubrics_str,
        quality_intent=quality_intent or "(none specified)",
        memory=memory or "(none)",
        dag=dag_summary,
    )

    result = call_llm_structured(prompt, schema=AllChecks)

    # Map back to NodeChecks per node (used by existing nodes table)
    for pc in result.nodes:
        _validate_checks(pc.checks)

    return result


def attach_checks_to_dag(dag: PlanDAG, all_checks: AllChecks) -> PlanDAG:
    """Attach generated checks back onto the DAG nodes in-place.

    Mutates the PlanNode objects to populate their ``checks`` list.
    Checks are stored in the ``extra`` dict since PlanNode doesn't have
    a native checks field (checks live in the DB nodes table).
    """
    check_map = {pc.node_id: pc.checks for pc in all_checks.nodes}
    for node in dag.nodes:
        node_checks = check_map.get(node.id, [])
        node.__dict__.setdefault("checks", node_checks)
    return dag


def _dag_for_prompt(dag: PlanDAG) -> str:
    """Render the DAG as a compact string for the LLM prompt."""
    lines = []
    for n in dag.nodes:
        members_str = ", ".join(
            f"{m.agent_config} ({m.role})" for m in n.members
        )
        lines.append(
            f"Node {n.id}: members=[{members_str}] "
            f"depends_on={n.depends_on} "
            f"task={n.task.text[:200]} "
            f"success={n.success.text[:200]}"
        )
    return "\n".join(lines)


def _validate_checks(checks: list[Check]) -> None:
    """Drop L1 checks with runtime leaks silently."""
    from backend.evaluator.generate import L1_RUNTIME_SIGNALS
    valid = []
    for c in checks:
        if c.tier == "L1" and c.check_cmd:
            cmd_lower = c.check_cmd.lower()
            if any(signal in cmd_lower for signal in L1_RUNTIME_SIGNALS):
                logger.warning(
                    "dropping leaked L1 check %s: %s",
                    c.id, c.check_cmd[:100],
                )
                continue
        valid.append(c)
    checks[:] = valid
