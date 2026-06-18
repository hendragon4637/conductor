"""Plan validation utilities — DAG acyclicity checks, validation helpers.

The three-tier PlanNode (kind/ref) has been REMOVED per v5.1 E2E spec.
Canonical Node/Plan models live in ``planning.schema``.
This file retains only the shared validation functions still needed.
"""
from __future__ import annotations

from typing import Any

from backend.planning.schema import PlanNode


def validate_plan(plan: list[PlanNode] | dict[str, Any], tool_registry: set[str] | None = None) -> None:
    """Validate a plan's DAG structure.

    Checks:
    - DAG is acyclic
    - Every node has a non-empty success criterion
    - Node IDs are unique

    Accepts either a list of PlanNode (canonical) or a dict with a ``dag`` key.

    Raises ValueError on first failure.
    """
    nodes: list[PlanNode]
    if isinstance(plan, dict):
        nodes = plan.get("dag", [])
    else:
        nodes = plan

    if not nodes:
        return

    # DAG acyclicity
    if not _is_acyclic(nodes):
        raise ValueError("Plan DAG contains a cycle")

    # Dependencies resolve
    node_ids = {n.id for n in nodes}
    for n in nodes:
        for dep in n.depends_on:
            if dep not in node_ids:
                raise ValueError(
                    f"Node {n.id} depends on {dep} which does not exist. "
                    f"Available: {node_ids}"
                )

    # Every node must have a non-empty success criterion
    for n in nodes:
        if not n.success.text.strip():
            raise ValueError(f"Node {n.id}: success criterion is empty")

    # Node IDs must be unique
    ids = [n.id for n in nodes]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate node IDs found")


def _is_acyclic(nodes: list[PlanNode]) -> bool:
    """Topological sort check — returns True if no cycle."""
    adj: dict[str, list[str]] = {n.id: list(n.depends_on) for n in nodes}
    visited: dict[str, int] = {}  # 0=white, 1=gray, 2=black

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
