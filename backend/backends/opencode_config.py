"""Per-worktree OpenCode config writer — writes node-specific opencode.json
and oh-my-openagent.jsonc into the worktree root at spawn time.

The config MERGES with global OpenCode config — only node-specific overrides
are written here. OpenCode's project-level config takes highest precedence.

Two agent_config types:
  - ``opencode`` (plain, class-b): single agent under AionUi orchestrator.
    ``OPENCODE_OMO=false`` + ``default_agent=<defined agent>`` guarantees
    the node uses the defined agent, NOT the global OMO setup.
  - ``opencode_omo`` (class-a): OMO self-orchestrates. ``OPENCODE_OMO=true``
    + OMO per-agent/category overrides in oh-my-openagent.jsonc.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def write_worktree_config(
    worktree: str | Path,
    *,
    model: str | None = None,
    permissions: dict[str, Any] | None = None,
    appended_prompt: str | None = None,
    agent_name: str = "backend-executor",
    agent_type: str = "opencode",
    omo_agents: dict[str, Any] | None = None,
    omo_categories: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write per-worktree OpenCode (and optionally OMO) config.

    Args:
        worktree: Path to the worktree root.
        model: Per-worktree model override (e.g. ``"openrouter/deepseek-v4-flash"``).
        permissions: Permission dict (e.g. ``{"edit": "allow", "bash": {"*": "allow"}}``).
        appended_prompt: Additional instructions for this node (written to
            ``.conductor/NODE_BRIEF.md`` and referenced via ``{file:}`` syntax).
        agent_name: Name of the primary agent for this node (becomes ``default_agent``).
        agent_type: ``"opencode"`` (plain, class-b) or ``"opencode_omo"`` (class-a).
        omo_agents: Per-agent OMO overrides (e.g. ``{"sisyphus": {"model": "..."}}``).
        omo_categories: Per-category OMO overrides (e.g. ``{"visual-engineering": {"model": "..."}}``).

    Returns:
        Dict with keys ``opencode_config_path`` and optionally ``omo_config_path``.
    """
    wt = Path(worktree)
    wt.mkdir(parents=True, exist_ok=True)

    conductor_dir = wt / ".conductor"
    conductor_dir.mkdir(parents=True, exist_ok=True)

    written: dict[str, Any] = {}

    if not model and agent_type.startswith("opencode"):
        model = "nvidia/gpt-oss-120b"

    # ── opencode.json ──────────────────────────────────────────────────────
    oc: dict[str, Any] = {
        "$schema": "https://opencode.ai/config.json",
    }

    if model:
        oc["model"] = model

    if permissions:
        oc["permission"] = permissions

    # Appended instructions — write NODE_BRIEF.md and reference it
    brief_path = conductor_dir / "NODE_BRIEF.md"
    current_brief = brief_path.read_text(encoding="utf-8") if brief_path.exists() else ""
    if appended_prompt and appended_prompt != current_brief:
        brief_path.write_text(appended_prompt, encoding="utf-8")

    instructions = []
    if brief_path.exists():
        instructions.append("{file:./.conductor/NODE_BRIEF.md}")
    if instructions:
        oc["instructions"] = instructions

    # Agent definition for THIS node
    agent_def: dict[str, Any] = {
        "mode": "primary",
    }
    if model:
        agent_def["model"] = model
    if permissions:
        agent_def["permission"] = permissions
    if brief_path.exists():
        agent_def["prompt"] = "{file:./.conductor/NODE_BRIEF.md}"
    oc["agent"] = {agent_name: agent_def}

    # CRITICAL: default_agent makes the defined agent the default
    oc["default_agent"] = agent_name

    # Toggle OMO mode based on agent_type
    omo_enabled = agent_type == "opencode_omo"

    oc_path = wt / "opencode.json"
    _write_json(oc_path, oc)
    written["opencode_config_path"] = str(oc_path)

    # ── oh-my-openagent.jsonc (OMO config) ────────────────────────────────
    if omo_enabled:
        omo: dict[str, Any] = {
            "$schema": "https://raw.githubusercontent.com/code-yeongyu/oh-my-openagent/dev/assets/oh-my-opencode.schema.json",
        }
        if omo_agents:
            omo["agents"] = omo_agents
        else:
            # Sensible defaults if none provided
            omo["agents"] = {
                agent_name: {"model": model or "nvidia/gpt-oss-120b"},
            }
        if omo_categories:
            omo["categories"] = omo_categories

        omo_path = wt / "oh-my-openagent.jsonc"
        _write_jsonc(omo_path, omo)
        written["omo_config_path"] = str(omo_path)

    return written


def spawn_env_for(agent_type: str) -> dict[str, str]:
    """Return environment variables to set when spawning a node.

    Args:
        agent_type: ``"opencode"`` or ``"opencode_omo"``.

    Returns:
        Dict of env vars (e.g. ``{"OPENCODE_OMO": "true"}``).
    """
    env: dict[str, str] = {}
    if agent_type == "opencode":
        env["OPENCODE_OMO"] = "false"
    elif agent_type == "opencode_omo":
        env["OPENCODE_OMO"] = "true"
    return env


def cleanup_worktree_config(worktree: str | Path) -> None:
    """Remove per-worktree config files from the worktree root.

    Safe to call during worktree teardown.
    """
    wt = Path(worktree)
    for f in ("opencode.json", "oh-my-openagent.jsonc"):
        p = wt / f
        if p.exists():
            p.unlink()
    conductor_dir = wt / ".conductor"
    brief = conductor_dir / "NODE_BRIEF.md"
    if brief.exists():
        brief.unlink()
    if conductor_dir.exists() and not any(conductor_dir.iterdir()):
        conductor_dir.rmdir()


# ── JSON / JSONC writers ────────────────────────────────────────────────────


def _write_json(path: Path, data: dict[str, Any]) -> None:
    """Write a compact JSON file."""
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _write_jsonc(path: Path, data: dict[str, Any]) -> None:
    """Write a JSONC file (JSON with comments support).

    For now uses standard JSON. OpenCode's JSONC parser handles it.
    """
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
