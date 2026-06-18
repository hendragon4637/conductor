"""Granularity heuristics — chunk sizing rules.

Supersedes the three-tier classification (tool/single_agent/team) from Files 17-18.
Every node is a team led by the built-in orchestrator; there is no tool/single_agent
distinction in the plan layer. Deterministic ops are internal Conductor built-ins.

Rules:
- A team chunk = a coherent unit finishable in one focused session
- Too big → split into multiple chunks
- Too small → merge with adjacent chunk
"""
from __future__ import annotations


def right_sized(chunk_desc: str, detail_level: str = "") -> tuple[bool, str]:
    """Check if a chunk description is appropriately sized.

    Returns ``(is_right_sized, reason)``.

    A team chunk should be a coherent unit finishable in one focused session:
    - Too big: "build the whole app", "implement everything"
    - Too small: "write one function", "add a single line"
    - Just right: "implement CRUD endpoints for users", "build login page"
    """
    desc_lower = chunk_desc.lower()

    # Too big signals
    too_big = ["whole", "entire", "all", "everything", "full", "complete"]
    for marker in too_big:
        if marker in desc_lower:
            return False, f"too broad ('{marker}' suggests oversized scope)"

    # Too small signals
    too_small = ["one function", "single line", "one method", "one variable"]
    for marker in too_small:
        if marker in desc_lower:
            return False, f"too narrow ('{marker}' suggests need more scope)"

    # Count sub-steps (commas, 'and', 'then')
    sub_step_count = (
        desc_lower.count(",")
        + desc_lower.count(" and ")
        + desc_lower.count(" then ")
        + desc_lower.count(";")
    )
    if sub_step_count > 5:
        return False, f"too many sub-steps ({sub_step_count}) — consider splitting"

    if not desc_lower.strip() or len(desc_lower.split()) < 3:
        return False, "description too short"

    return True, "appropriately sized"
