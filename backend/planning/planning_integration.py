"""Integration points for the Planning Standard (simplified).

Remaining hooks:
1. L2 rubric augmentation → get_gate_rubric_item() from planning_standard
2. Continuation goal reference → prior decisions context from .memory/
3. Retry remediation context
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from backend.planning.planning_standard import get_gate_rubric_item

logger = logging.getLogger(__name__)


def augment_l2_rubric(existing_rubric: dict[str, Any] | None) -> dict[str, Any]:
    """Add the justifies_decomposition rubric item to the plan L2 rubric."""
    rubric = existing_rubric or {
        "name": "plan_structure",
        "items": [],
    }
    rubric_item = get_gate_rubric_item()
    if not any(item["id"] == rubric_item["id"] for item in rubric["items"]):
        rubric["items"].append(rubric_item)
    return rubric


def on_continuation_goal(prior_plan_id: str, memory_dir: str) -> str:
    """Build continuation context from prior decisions.

    Args:
        prior_plan_id: The plan_id of the previous run.
        memory_dir: Path to the .memory/ directory.

    Returns:
        Context string about prior decisions, or empty string if none.
    """
    memory_file = Path(memory_dir) / "decisions.md"
    if not memory_file.exists():
        return ""

    content = memory_file.read_text(encoding="utf-8", errors="replace")
    blocks = content.split("---")
    latest = blocks[-1].strip() if len(blocks) >= 2 else content.strip()

    return f"Previous plan ({prior_plan_id}) decisions:\n{latest[:2000]}"


def on_remediation(worktree: str, failed_checks: list[dict[str, Any]]) -> str:
    """Build remediation context from failed checks.

    Args:
        worktree: Path to the planning worktree.
        failed_checks: Failed L1/L2 checks from the gate.

    Returns:
        Additional remediation instruction string.
    """
    failed_ids = {
        c.get("check", c.get("check_id", ""))
        for c in failed_checks
        if not c.get("passed", c.get("criteria_met", True))
    }

    if not failed_ids:
        return ""

    extra = "\n\n[REMEDIATION CONTEXT]\n"
    extra += f"Failed checks to address: {', '.join(failed_ids)}\n"
    extra += "Ensure your revised plan addresses each failure."

    return extra
