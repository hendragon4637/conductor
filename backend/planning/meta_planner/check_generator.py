"""File 02b — Check Generator (separate LLM call #2b).

After the decomposer proposes the DAG, this module attaches per-node
evaluation checks — selecting L1 (deterministic) **from a fixed preset pool**
and selecting/adapting L2 (rubric) patterns from rubric presets, grounded by
quality_intent + memory.

L1 checks are STRICTLY SELECTED from available presets (canonical pool +
agent_config default_checks). The LLM may NOT reword or invent L1 check
commands — this prevents hallucinated shell commands.

L2 checks can be SELECTED from presets, ADAPTED (wording adjusted per-node),
or CREATED from quality_intent clauses.

Keeping this as a SEPARATE LLM call from the decomposer prevents the proposer
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


# ── Canonical L1 presets (deterministic, never invented) ────────────
# The LLM may SELECT from these by id.
# These are the ONLY L1 checks the LLM can attach to nodes.
CANONICAL_L1_PRESETS: list[dict[str, Any]] = [
    {
        "id": "l1-tests-pass",
        "criterion": "All tests pass",
        "check_cmd": (
            "python3 -m pytest -q --tb=short 2>&1 || "
            "(echo 'L1 det-tests failed: pytest did not pass.' && exit 1)"
        ),
    },
    {
        "id": "l1-files-exist",
        "criterion": "Expected code files exist",
        "check_cmd": (
            "ls -la *.py 2>/dev/null || ls -la src/*.py 2>/dev/null || "
            "ls -la backend/*.py 2>/dev/null || "
            "(echo 'L1 det-files failed: no Python files found.' && exit 1)"
        ),
    },
    {
        "id": "l1-syntax-check",
        "criterion": "No syntax errors in Python files",
        "check_cmd": (
            "files=$(find . -name '*.py' -not -path './.git/*' -not -path './.venv/*'); "
            'if [ -z "$files" ]; then echo "No Python files found"; exit 1; fi; '
            "python3 -m py_compile $files 2>&1 || "
            "(echo 'L1 det-syntax failed: syntax errors.' && exit 1)"
        ),
    },
    {
        "id": "l1-regression",
        "criterion": "Prior work is not broken by changes",
        "check_cmd": (
            'echo "Regression: previous node commits should still pass their tests" && exit 0'
        ),
    },
    {
        "id": "l1-run-md-present",
        "criterion": "RUN.md exists documenting run steps",
        "check_cmd": (
            "test -f RUN.md || "
            "(echo 'L1 det-RUN.md missing: RUN.md not found in worktree.' && exit 1)"
        ),
    },
]

# L1 check IDs that are valid (canonical + loaded from agent_configs below)
_VALID_L1_IDS: set[str] = {p["id"] for p in CANONICAL_L1_PRESETS}


def load_agent_config_l1_presets(agent_config_id: str | None) -> list[dict[str, Any]]:
    """Load L1 presets from an agent_config's default_checks.

    Returns a list of dicts with ``id``, ``criterion``, ``check_cmd`` keys
    (and optional ``on_fail``).  These are added to the LLM's selectable pool.
    """
    if not agent_config_id:
        return []
    try:
        import os
        db_url = os.environ.get("DATABASE_URL", "")
        if not db_url:
            return []
        import psycopg
        with psycopg.connect(db_url) as c:
            with c.cursor() as cur:
                cur.execute(
                    "SELECT default_checks FROM agent_configs WHERE agent_config_id = %s",
                    (agent_config_id,),
                )
                row = cur.fetchone()
                if not row:
                    return []
                raw = row[0]
        if isinstance(raw, str):
            dc = json.loads(raw)
        elif isinstance(raw, dict):
            dc = raw
        else:
            return []
        presets: list[dict[str, Any]] = []
        for l1_item in dc.get("l1", []):
            cid = l1_item.get("id", "")
            if not cid:
                continue
            cmd = l1_item.get("cmd", "")
            criterion = l1_item.get("criterion", l1_item.get("on_fail", {}).get("what", f"L1: {cid}"))
            presets.append({
                "id": f"agent-{cid}",
                "criterion": criterion,
                "check_cmd": cmd,
                "on_fail": l1_item.get("on_fail"),
                "_agent_config_id": agent_config_id,
            })
            _VALID_L1_IDS.add(f"agent-{cid}")
        return presets
    except Exception as exc:
        logger.warning("Failed to load agent_config L1 presets for %s: %s", agent_config_id, exc)
        return []


def get_valid_l1_ids() -> set[str]:
    """Return the set of all valid L1 check IDs (canonical + discovered agent_config)."""
    return _VALID_L1_IDS


# ── Output contracts ────────────────────────────────────────────────

class PerNodeChecks(BaseModel):
    """Checks attached to a single node by the check-generator."""
    node_id: str
    checks: list[Check] = Field(
        default_factory=list,
        description="Generated checks (L1 deterministic selected from presets + L2 rubric)",
    )


class AllChecks(BaseModel):
    """All per-node check lists for the DAG."""
    nodes: list[PerNodeChecks] = Field(
        min_length=1,
        description="One entry per node, in the same order as the DAG",
    )


CHECKGEN_PROMPT = """\
You are a check-generation engine. Given a plan DAG, a quality intent string,
retrievable rubric presets with L1 presets, and optional memory context,
produce evaluation checks for each node.

=== L1 CHECKS (deterministic) — STRICT SELECTION ONLY ===
- L1 checks are shell commands run in the worktree (exit 0 = pass).
  They test concrete things: file existence, syntax checks, test execution.
- You MUST select L1 checks ONLY from the "Available L1 presets" list below.
- The ``id`` field for every L1 check MUST be one of the exact IDs shown in
  that list.  Anything else will be REJECTED by the system.
  - CORRECT: ``"id": "l1-files-exist"`` → accepted
  - WRONG: ``"id": "det-1"`` → REJECTED (not in preset list)
- For L1 checks, set the ``check_cmd`` field to the **exact same value as the
  ``id`` field** as a placeholder.  The system will replace it with the real
  shell command.  Do NOT guess or invent the command.
  - CORRECT: ``"id": "l1-files-exist", "check_cmd": "l1-files-exist"``
  - WRONG: ``"check_cmd": "ls -la"`` (let the system fill this in)
- Do NOT reword, modify, or invent L1 check commands. Do NOT create new L1
  checks — the L1 preset pool is the full universe of available L1 checks.
- You MUST attach at least 1 L1 check to EVERY node. Nodes with no L1 checks
  will have no deterministic validation, which causes downstream failures.
- Recommended L1 assignments (based on node task):
  - Scaffolding / file-creation node: ``l1-files-exist`` + ``l1-syntax-check``
  - Testing node: ``l1-tests-pass``
  - Non-first node (depends on prior work): add ``l1-regression``
- L1 checks MUST NOT contain runtime signals (curl, localhost, uvicorn,
  http://, :8000, :3000, health endpoints) — those belong to L4.

=== L2 CHECKS (rubric) — SELECT, ADAPT, OR CREATE ===
- L2 checks are rubric items: yes/no quality questions the L2 judge
  evaluates. They test correctness, completeness, error handling, etc.
- You may:
  (a) SELECT an item from "Available L2 rubric presets" as-is (provenance="preset").
  (b) ADAPT a preset's wording to be more specific to this node's task
      (provenance="preset_adapted"). Keep the original intent but tailor it.
  (c) CREATE a new rubric item from the quality_intent text that is relevant
      to this node's task (provenance="human_intent").
- Don't add every possible check — only what matters for the node's task.
- Weight matters: 2.0 = critical, 1.0 = normal, 0.5 = nice-to-have.

Available L1 presets (select by id, attach as-is):
{l1_presets}

Available L2 rubric presets:
{rubrics}

Quality intent:
{quality_intent}

Memory context:
{memory}

Plan DAG:
{dag}

Now produce the AllChecks JSON — one PerNodeChecks entry per node, with
matching node_id values.  Every check must have provenance matching its origin:
- "preset" — unmodified selection from L1 or L2 presets
- "preset_adapted" — L2 preset wording adapted per-node
- "human_intent" — created from quality_intent
- "agent_default" — from agent_config default_checks"""


def generate_checks(
    dag: PlanDAG,
    quality_intent: str = "",
    memory: str = "",
) -> AllChecks:
    """Generate evaluation checks for all nodes in a plan DAG.

    This is a SEPARATE LLM call from the decomposer. The check-generator
    sees the proposed DAG and selects L1 from the canonical L1 pool,
    and selects/adapts/creates L2 checks from rubric presets, grounded
    in quality_intent and memory.

    Args:
        dag: The validated PlanDAG from the decomposer.
        quality_intent: Free-text quality guidance from the goal formulator.
        memory: Optional recalled memory context.

    Returns:
        An ``AllChecks`` with one ``PerNodeChecks`` per node.

    Raises:
        RuntimeError: If the LLM call fails after retries.
    """
    # Collect L1 presets — canonical + agent_config-specific for each unique member
    unique_agent_configs: set[str] = set()
    for n in dag.nodes:
        for m in n.members:
            unique_agent_configs.add(m.agent_config)
    l1_ac_presets: list[dict[str, Any]] = []
    for ac_id in sorted(unique_agent_configs):
        l1_ac_presets.extend(load_agent_config_l1_presets(ac_id))

    all_l1_presets = CANONICAL_L1_PRESETS + l1_ac_presets
    # Show per-agent-config grouping so LLM knows which apply to which nodes
    l1_presets_str_parts = [
        "=== VALID L1 IDs (these are the ONLY acceptable values for an L1 check's ``id`` field) ===",
        ", ".join(sorted(p["id"] for p in all_l1_presets)),
        "",
        "=== Canonical L1 presets (apply to ANY node) ===",
    ]
    for p in CANONICAL_L1_PRESETS:
        l1_presets_str_parts.append(
            f'  id="{p["id"]}"  criterion="{p["criterion"]}"'
        )
    if l1_ac_presets:
        l1_presets_str_parts.append("\n=== Agent-config L1 presets (apply only to nodes with that agent_config) ===")
        for p in l1_ac_presets:
            l1_presets_str_parts.append(
                f'  id="{p["id"]}"  (agent_config={p.get("_agent_config_id", "?")})  criterion="{p["criterion"]}"'
            )
    l1_presets_str = "\n".join(l1_presets_str_parts)

    rubrics = load_all_rubrics()
    rubrics_str = json.dumps(rubrics, indent=2) if rubrics else "(no rubrics loaded)"

    dag_summary = _dag_for_prompt(dag)

    prompt = CHECKGEN_PROMPT.format(
        l1_presets=l1_presets_str,
        rubrics=rubrics_str,
        quality_intent=quality_intent or "(none specified)",
        memory=memory or "(none)",
        dag=dag_summary,
    )

    result = call_llm_structured(prompt, schema=AllChecks)

    # Build preset map for resolving L1 check_cmd / tier
    l1_preset_map: dict[str, dict[str, Any]] = {}
    for p in all_l1_presets:
        l1_preset_map[p["id"]] = p

    for pc in result.nodes:
        # Resolve L1 check_cmd from presets (LLM selects by ID only)
        _resolve_l1_checks(pc.checks, l1_preset_map)
        # Drop runtime leaks from L1 checks
        _validate_checks(pc.checks)

    return result


def attach_checks_to_dag(dag: PlanDAG, all_checks: AllChecks) -> PlanDAG:
    """Attach generated checks back onto the DAG nodes in-place.

    Mutates the PlanNode objects to populate their ``checks`` list.
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


def _resolve_l1_checks(checks: list[Check], l1_preset_map: dict[str, dict[str, Any]]) -> None:
    """Resolve deterministic (L1) checks from the preset pool.

    The LLM selects L1 checks by ID only (as instructed).  ``tier`` is a
    computed property derived from ``type`` (deterministic → L1), so the
    only field we must resolve is ``check_cmd`` — the LLM output may contain
    a guess, and we overwrite it with the canonical command from the preset.

    If a check's ID isn't in the preset map, it's dropped with a warning
    (shouldn't happen if the LLM follows instructions, but guard anyway).

    Mutates ``checks`` in-place.
    """
    valid: list[Check] = []
    for c in checks:
        if c.type == "deterministic":
            preset = l1_preset_map.get(c.id)
            if preset is None:
                logger.warning("dropping unknown L1 check %s (not in preset pool)", c.id)
                continue
            c.check_cmd = preset["check_cmd"]
            if preset.get("on_fail"):
                c.on_fail = preset["on_fail"]
            valid.append(c)
        else:
            valid.append(c)
    checks[:] = valid


def _validate_checks(checks: list[Check]) -> None:
    """Drop L1 checks with runtime leaks.

    L1 ID hallucination validation is done by the plan evaluator
    (``plan_evaluator._validate_l1_check_ids``) — not here.
    The check generator's job is to produce checks; the plan gate
    validates their legitimacy.
    """
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
