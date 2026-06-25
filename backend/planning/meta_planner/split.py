from __future__ import annotations

import logging
import os
from typing import Any

from backend.planning.meta_planner.decomposer import PlanNode, PlanDAG, Member, NodeTask, NodeSuccess
from backend.planning.meta_planner.llm import call_llm_structured
from backend.planning.can_redecompose import MAX_DEPTH

logger = logging.getLogger(__name__)

NODE_SIZE_CAP = int(os.environ.get("NODE_SIZE_CAP", "24000"))

SPLIT_PROMPT = """\
You are a node-splitting engine. Given a single oversized plan node and its
parent meta-goal context, break it into a flat, ordered sub-sequence of 2-4
smaller nodes that together achieve the same outcome.

Each child node must:
- Be scoped to ONE focused unit of work
- Have its own `size_estimate` (smaller than the parent's)
- Depend on the previous child in sequence (sequential deps)
- Use the SAME agent_config members and backend as the parent

Output a JSON array of node objects. Each object has:
  id, members, depends_on, task (text/inputs/deliverables),
  success (text), size_estimate

Parent node task:
{task_text}

Parent node success criterion:
{success_text}

Parent members (use exactly these):
{members}

Meta-goal context:
{goal}

Spec:
{spec}"""


# ── Pydantic output schema for the split result ──────────────────────

from pydantic import BaseModel, Field


class SplitResult(BaseModel):
    """Flat sub-sequence output from splitting one oversized node."""
    nodes: list[PlanNode] = Field(min_length=2, max_length=8)


# ── Main entry point ─────────────────────────────────────────────────

def split_oversized(dag: PlanDAG, meta_goal: Any) -> PlanDAG:
    """Plan-time node splitting (File 07).

    Iterates the DAG and splits any node whose ``size_estimate`` exceeds
    ``NODE_SIZE_CAP`` into a flat sub-sequence. Repeats until no node
    exceeds the cap (bounded by MAX_DEPTH).

    Args:
        dag: The PlanDAG from the decomposer.
        meta_goal: The MetaGoal object (provides goal/spec context).

    Returns:
        The modified PlanDAG with oversized nodes replaced.
    """
    if not dag or not dag.nodes:
        return dag

    goal = getattr(meta_goal, "goal", "")
    spec = getattr(meta_goal, "spec", "")
    changed = True
    iteration = 0
    max_iterations = 20  # safety cap

    while changed and iteration < max_iterations:
        changed = False
        iteration += 1
        to_split = [n for n in dag.nodes if _should_split(n)]
        if not to_split:
            break

        for node in to_split:
            if node.node_status != "active":
                continue
            children = _decompose_node(node, goal, spec)
            if not children or len(children) < 2:
                logger.warning(
                    "Split of node %s produced %d children — skipping",
                    node.id, len(children) if children else 0,
                )
                continue

            # Mark parent as superseded
            node.node_status = "superseded"

            # Wire children: sequential deps + parent metadata
            for i, c in enumerate(children):
                c.parent_node_id = node.id
                c.depth = node.depth + 1
                if i == 0:
                    c.depends_on = list(node.depends_on)
                else:
                    c.depends_on = [children[i - 1].id]

            # Rewire dependents: nodes that depended on parent now depend on last child
            for other in dag.nodes:
                if other.id != node.id and node.id in other.depends_on:
                    other.depends_on = [
                        children[-1].id if d == node.id else d
                        for d in other.depends_on
                    ]

            # Insert children after parent in the node list
            insert_at = _node_index(dag, node.id)
            dag.nodes = dag.nodes[:insert_at] + children + dag.nodes[insert_at + 1:]
            changed = True

        # Validate acyclicity after each pass
        _assert_acyclic(dag)

    if iteration >= max_iterations:
        logger.warning("split_oversized hit iteration cap (%d)", max_iterations)

    return dag


# ── Helpers ──────────────────────────────────────────────────────────

def _should_split(node: PlanNode) -> bool:
    if node.node_status != "active":
        return False
    if node.depth >= MAX_DEPTH:
        return False
    if node.size_estimate <= NODE_SIZE_CAP:
        return False
    return True


def _decompose_node(node: PlanNode, goal: str, spec: str) -> list[PlanNode]:
    """Call LLM to split one oversized node into a flat sub-sequence."""
    members_json = ", ".join(
        f"{m.agent_config} ({m.role})" for m in node.members
    )
    prompt = SPLIT_PROMPT.format(
        task_text=node.task.text,
        success_text=node.success.text,
        members=members_json,
        goal=goal,
        spec=spec or "(none)",
    )
    try:
        result = call_llm_structured(prompt, schema=SplitResult)
        return result.nodes
    except Exception as exc:
        logger.exception("Failed to split node %s: %s", node.id, exc)
        return []


def _node_index(dag: PlanDAG, node_id: str) -> int:
    for i, n in enumerate(dag.nodes):
        if n.id == node_id:
            return i
    return -1


def _assert_acyclic(dag: PlanDAG) -> None:
    """Raise ValueError if the DAG contains a cycle."""
    node_ids = {n.id for n in dag.nodes}
    adj: dict[str, list[str]] = {}
    for n in dag.nodes:
        adj[n.id] = [d for d in n.depends_on if d in node_ids]
    visited: dict[str, int] = {}

    def _dfs(nid: str) -> bool:
        if nid in visited:
            return visited[nid] == 2
        visited[nid] = 1
        for dep in adj.get(nid, []):
            if dep in visited and visited[dep] == 1:
                raise ValueError(f"Cycle detected involving node {nid} → {dep}")
            if dep not in visited:
                if not _dfs(dep):
                    return False
        visited[nid] = 2
        return True

    for nid in adj:
        if nid not in visited:
            _dfs(nid)
