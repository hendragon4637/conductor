import json
from pathlib import Path
from typing import Any

from .base import CLIAdapter


def _allow_all_config() -> dict[str, Any]:
    return {
        "$schema": "https://opencode.ai/config.json",
        "permission": {
            "edit": "allow",
            "bash": "allow",
            "webfetch": "allow",
        },
    }


def _rules_to_permission_config(rules: dict[str, Any]) -> dict[str, Any]:
    """Convert a rules dict into an opencode.json permission block.

    The ``mode`` key (if present) controls auto-approve behaviour.
    All other keys are treated as direct permission entries.
    """
    mode = rules.pop("mode", "")
    if mode == "auto_approve":
        return _allow_all_config()

    # Mode is "ask" or unset — write the rules as-is as permission entries.
    # An empty rules dict produces an empty permission block, which lets
    # OpenCode fall back to the global config.
    return {
        "$schema": "https://opencode.ai/config.json",
        "permission": rules,
    }


class OpenCodeAdapter(CLIAdapter):
    engine = "opencode"

    def write_permission(self, worktree: Path, rules: dict[str, Any]) -> Path:
        dest = worktree / "opencode.json"
        config = _rules_to_permission_config(dict(rules))
        dest.write_text(json.dumps(config, indent=2))
        return dest

    def write_instructions(self, worktree: Path, content: str) -> Path:
        dest = worktree / "AGENTS.md"
        dest.write_text(content)
        return dest

    def write_skills(self, worktree: Path, skills: dict[str, str]) -> list[Path]:
        written = []
        for name, markdown in skills.items():
            dest = worktree / ".opencode" / "skills" / name / "SKILL.md"
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(markdown)
            written.append(dest)
        return written

    def aionui_preset_agent_type(self) -> str:
        return "acp"

    def aionui_assistant_id(self) -> str | None:
        return "53861a53"
