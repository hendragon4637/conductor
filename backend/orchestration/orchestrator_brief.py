from __future__ import annotations

import os
from typing import Any


POLL_MIN_SECONDS = int(os.environ.get("ORCHESTRATOR_POLL_MIN_SECONDS", "30"))


def _node_description(node: dict[str, Any]) -> str:
    task = node.get("task")
    if isinstance(task, dict):
        return task.get("text", "")
    if isinstance(task, str):
        return task
    return node.get("description") or node.get("title", "")


def _node_success(node: dict[str, Any]) -> str:
    success = node.get("success")
    if isinstance(success, dict):
        return success.get("text", "")
    if isinstance(success, str):
        return success
    return node.get("success_criterion") or _node_description(node)


def _format_members(members: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for member in members:
        role = member.get("role", "member")
        agent_id = member.get("agent_config_id", "unknown")
        task = member.get("task", "")
        success = member.get("success", "")
        lines.append(f"- {agent_id}: {role}")
        if task:
            lines.append(f"  Task: {task}")
        if success:
            lines.append(f"  Success criterion: {success}")
    return "\n".join(lines) if lines else "- (no members configured)"


def _derive_assignments(node: dict[str, Any], members: list[dict[str, Any]]) -> str:
    if not members:
        return "1) No team members available. Escalate instead of doing the work yourself."

    lines: list[str] = []
    for idx, member in enumerate(members, start=1):
        agent_id = member.get("agent_config_id", "unknown")
        role = str(member.get("role", "member"))
        raw_task = member.get("task") or node.get("task") or node.get("title") or node.get("description", "")
        task = raw_task.get("text") if isinstance(raw_task, dict) else str(raw_task)
        deps = member.get("depends_on", []) or []
        if idx == 1:
            prefix = f"{idx}) Assign {task} to {agent_id}."
        else:
            if "review" in role or "qa" in role:
                prefix = f"{idx}) After earlier member work is complete, assign validation/review to {agent_id}."
            else:
                prefix = f"{idx}) After prerequisite work is ready, assign {task} to {agent_id}."
        if deps:
            prefix += f" Wait for dependencies: {', '.join(str(d) for d in deps)}."
        lines.append(prefix)
    lines.append(f"{len(members) + 1}) When the success criterion is satisfied, report completion succinctly and stop.")
    return "\n".join(lines)


def build_orchestrator_brief(
    node: dict[str, Any],
    members: list[dict[str, Any]],
    dep_context: str,
) -> str:
    success = _node_success(node)
    description = _node_description(node)
    return f"""ROLE: You are the orchestrator. You COORDINATE; you do NOT implement, edit files, or run code yourself.

TASK:
{description}

HARD RULES:
- You MUST delegate all work to your team MEMBERS listed below. Do NOT spawn your own subagents.
- Do NOT write code, edit files, or run bash yourself. If you are about to act directly, STOP and delegate instead.
- Delegate to a member by assigning a specific task to that member by name.
- Conductor monitors execution out-of-band; you do not need to watch members closely.

TEAM MEMBERS (delegate only to these):
{_format_members(members)}

WORKFLOW FOR THIS NODE:
Task: {description}
Success criterion: {success}

Assignment plan:
{_derive_assignments(node, members)}

DEPENDENCY CONTEXT (already-completed prior work):
{dep_context or '(none)'}

MONITORING DISCIPLINE:
- Check member progress AT MOST once every {POLL_MIN_SECONDS} seconds.
- Do NOT poll continuously.

DONE CRITERIA:
- The node is complete when: {success}
- Report completion succinctly, then stop.
"""


def build_single_agent_lead_brief(
    node: dict[str, Any],
    dep_context: str,
) -> str:
    description = _node_description(node)
    success = _node_success(node)
    role = node.get("role") or node.get("agent_config") or "executor"
    parts = [
        f"ROLE: You are the team lead and sole implementer for this node ({role}).",
        "",
        "TASK:",
        description,
        "",
        "REQUIREMENTS:",
        f"- Complete the task directly in the workspace.",
        f"- Satisfy this success criterion exactly: {success}",
        "- Use the existing codebase patterns and keep changes focused.",
        "- Do NOT ask clarifying questions or request approval. Decide autonomously and execute.",
        "- You have full authority to create, modify, or delete files in the workspace.",
        "- When the task is complete, report completion succinctly and stop.",
        "",
        "DEPENDENCY CONTEXT (already-completed prior work):",
        dep_context or "(none)",
    ]
    return "\n".join(parts)
