"""File 02 — Decomposer: meta-goal → plan-node DAG with real agent_config roster.

Separate LLM call (#2) from check-generation. Proposes WHAT to build + WHO
builds it. Does NOT define how it's judged (that's the check-generator's job).

Key constraints:
  - Every ``member.agent_config`` is from the REAL roster (never invented).
  - DAG is validated (acyclic, deps resolve).
  - Model is config-driven (same ``meta_planner`` role).
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import psycopg
from pydantic import BaseModel, Field, model_validator

from backend.planning.meta_planner.llm import call_llm_structured

logger = logging.getLogger(__name__)


# ── Output contracts ─────────────────────────────────────────────────────────

class Member(BaseModel):
    """A team member assigned to a node, referencing a real agent_config."""
    agent_config: str = Field(
        description="Agent config ID from the real roster — MUST be in the allowed set",
    )
    backend: str = Field(
        default="opencode",
        description="Execution backend: opencode, opencode_omo, hermes, claude_code, etc.",
    )
    role: str = Field(
        default="executor",
        description="Function on this node: executor, reviewer, planner, etc.",
    )


class NodeTask(BaseModel):
    """What THIS specific node does — scoped, not the plan goal."""
    text: str = Field(description="What this node does, bounded and specific to this node")
    inputs: list[str] = Field(default_factory=list, description="Files/artifacts this node needs")
    deliverables: list[str] = Field(default_factory=list, description="Concrete outputs it must produce")


class NodeSuccess(BaseModel):
    """Prose success criterion for this node.
    All verifiable conditions live in checks (generated separately).
    """
    text: str = Field(description="Human-readable measurable success criterion for this node")


class PlanNode(BaseModel):
    """A single node in the plan DAG."""
    id: str = Field(description="Unique node id, e.g. 'node-1'")
    members: list[Member] = Field(min_length=1, description="Team members for this node (>=1)")
    depends_on: list[str] = Field(default_factory=list, description="Node IDs this node depends on")
    task: NodeTask = Field(description="Node-scoped task")
    success: NodeSuccess = Field(description="Prose success criterion")
    size_estimate: int = Field(
        default=0,
        description="Decomposer's estimate of change volume in chars (File 07)",
    )
    parent_node_id: str | None = Field(
        default=None,
        description="Set when this node was created by splitting an oversized parent (File 07)",
    )
    depth: int = Field(
        default=0,
        description="Nesting depth: 0 for top-level nodes, incremented on split (File 07)",
    )
    node_status: str = Field(
        default="active",
        description="'active' | 'superseded' — superseded means this node was split into children",
    )


class PlanDAG(BaseModel):
    """The full plan DAG output from the decomposer."""
    nodes: list[PlanNode] = Field(min_length=1, description="At least one node in the DAG")

    @model_validator(mode="after")
    def _validate_dag(self) -> "PlanDAG":
        node_ids = {n.id for n in self.nodes}
        for n in self.nodes:
            for dep in n.depends_on:
                if dep not in node_ids:
                    raise ValueError(
                        f"Node '{n.id}' depends on '{dep}' which does not exist. "
                        f"Available nodes: {node_ids}"
                    )
        if not _is_acyclic(self.nodes):
            raise ValueError("DAG contains a cycle")
        return self


# ── Roster enum ──────────────────────────────────────────────────────────────

def roster_enum(domain: str | None = None) -> list[dict[str, Any]]:
    """Fetch real agent_config roster for the decomposer prompt.

    Returns a small list of ``{agent_config_id, role, backend, description}``
    — NOT full agent bodies. The goal is to keep the prompt lean while
    giving the decomposer enough context to assign members correctly.

    Args:
        domain: Optional domain filter (e.g. ``"backend"``, ``"frontend"``).
            If omitted, returns all available configs.

    Returns:
        List of agent config summaries. Empty list on error (graceful
        degradation — the decomposer prompt should handle this).
    """
    try:
        db_url = os.environ.get("DATABASE_URL", "")
        if not db_url:
            raise RuntimeError("DATABASE_URL not set")
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT agent_config_id, domain, role, harness "
                    "FROM agent_configs WHERE active = true ORDER BY agent_config_id"
                )
                rows = cur.fetchall()
        result = []
        for r in rows:
            result.append({
                "agent_config_id": r[0],
                "domain": r[1],
                "role": r[2],
                "harness": r[3],
                "backend": "opencode",
                "description": f"{r[1]} / {r[2]} / {r[3]}",
            })
        return result
    except Exception:
        logger.exception("failed to list agent configs")
        return []


# ── Prompt template ──────────────────────────────────────────────────────────

DECOMPOSE_PROMPT = """\
You are a plan-decomposition engine. Given a formulated meta-goal and a
roster of available agent configs, break the work into a DAG of plan nodes.

CRITICAL RULES — read carefully:
  1. Each node's members MUST use agent_config IDs EXACTLY as listed in the
     roster below. Copy them verbatim. NEVER invent, abbreviate, or modify
     an agent_config ID. If the roster has "finance-fullstack-executor",
     use that exact string — NOT "finance-executor".
  2. The ONLY valid agent_config IDs are those in the roster below. Do NOT use
     any other value.
  3. Node-scoped tasks: each node does ONE thing. The task text must be
     specific to that node, not copied from the plan goal.
  4. Dependencies must be correct and minimal.
  5. The DAG must be acyclic.
  6. A node's success criterion is a prose statement of what "done" means
     for that node. It will be used later to generate evaluation checks.
  7. Do NOT generate checks here — only structure, members, and success criteria.
  8. For each node, set `size_estimate` = approximate number of characters of
     code/content this node will create or change. Be realistic: a node
     implementing a full module is larger than one adding a single function.
     Nodes over 24000 chars may be split automatically.

Meta goal:
{goal}

Spec / constraints:
{spec}

Quality intent (will inform check generation):
{quality_intent}

Available agent configs — choose members ONLY from these IDs (copy EXACTLY):
{roster}

Valid agent_config_id values (use these EXACT strings, nothing else):
{valid_ids}

Recalled memory context:
{memory}

{revision_block}
Now produce the PlanDAG JSON."""


# ── Implementation ───────────────────────────────────────────────────────────

def decompose(
    goal: str,
    spec: str = "",
    quality_intent: str = "",
    memory: str = "",
    domain: str | None = None,
    feedback: str = "",
    prior_dag: list | None = None,
) -> PlanDAG:
    """Meta-goal → PlanDAG (LLM call #2).

    When called on a revision cycle, ``feedback`` and ``prior_dag`` carry the
    gate evaluator's failure diagnosis and the previous attempt's DAG, so the
    LLM can fix specific issues rather than regenerating from scratch.

    Args:
        goal: Normalized one-sentence objective from the goal formulator.
        spec: Constraints/acceptance criteria.
        quality_intent: Quality guidance (passed through to check-gen later).
        memory: Optional recalled memory context.
        domain: Optional domain filter for agent_config roster.
        feedback: Gate evaluator feedback text from the previous attempt
            (empty on first call).
        prior_dag: Previous attempt's DAG node list (None on first call).

    Returns:
        A validated ``PlanDAG`` with real agent_config references.

    Raises:
        ValueError: If a member references an unknown agent_config.
        RuntimeError: If the LLM call fails after retries.
    """
    roster = roster_enum(domain)
    roster_str = json.dumps(roster, indent=2) if roster else "(no agent configs available)"
    valid_ids_str = "\n".join(f"  - {r['agent_config_id']}" for r in roster) if roster else "  (none)"

    # Build revision block if this is a re-decompose cycle
    revision_block = ""
    if feedback or prior_dag:
        parts = []
        if prior_dag:
            import json as _json
            parts.append(
                "PREVIOUS DAG (failed gate evaluation):\n"
                + _json.dumps(prior_dag, indent=2)
            )
        if feedback:
            parts.append(
                "GATE FEEDBACK — address these failures:\n"
                + feedback
            )
        parts.append(
            "Fix each failure above. Keep the node structure that worked; "
            "only change what needs fixing. Do NOT regenerate from scratch "
            "— retain good node boundaries, dependencies, and agent assignments "
            "from the previous DAG where they still satisfy the goal."
        )
        revision_block = "\n\n".join(parts)

    prompt = DECOMPOSE_PROMPT.format(
        goal=goal,
        spec=spec or "(none specified)",
        quality_intent=quality_intent or "(none specified)",
        roster=roster_str,
        valid_ids=valid_ids_str,
        memory=memory or "(none)",
        revision_block=revision_block,
    )

    dag = call_llm_structured(prompt, schema=PlanDAG)

    # Validate members against real roster
    valid_ids = {r["agent_config_id"] for r in roster}
    if valid_ids:
        for n in dag.nodes:
            for m in n.members:
                if m.agent_config not in valid_ids:
                    raise ValueError(
                        f"Node '{n.id}': agent_config '{m.agent_config}' is not in the real roster. "
                        f"Valid IDs: {valid_ids}"
                    )

    return dag


# ── Helpers ──────────────────────────────────────────────────────────────────

def _is_acyclic(nodes: list[PlanNode]) -> bool:
    adj: dict[str, list[str]] = {n.id: list(n.depends_on) for n in nodes}
    visited: dict[str, int] = {}

    def _dfs(nid: str) -> bool:
        if nid in visited:
            return visited[nid] == 2
        visited[nid] = 1
        for dep in adj.get(nid, []):
            if dep in visited and visited[dep] == 1:
                return False
            if dep not in visited:
                if not _dfs(dep):
                    return False
        visited[nid] = 2
        return True

    for nid in adj:
        if nid not in visited:
            if not _dfs(nid):
                return False
    return True
