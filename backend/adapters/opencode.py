import json
from pathlib import Path
from typing import Any

from .base import CLIAdapter


def _allow_all_config(worktree: Path | None = None) -> dict[str, Any]:
    config: dict[str, Any] = {
        "$schema": "https://opencode.ai/config.json",
        "permission": {
            "bash": "allow",
            "webfetch": "allow",
        },
    }
    # Scope edit to the worktree path so the agent cannot write files
    # outside it.  This prevents stray `mkdir app` at the workspace root.
    if worktree is not None:
        config["permission"]["edit"] = {
            "allow": [f"{worktree}/**"],
        }
    else:
        config["permission"]["edit"] = "allow"
    return config


def _rules_to_permission_config(rules: dict[str, Any], worktree: Path | None = None) -> dict[str, Any]:
    """Convert a rules dict into an opencode.json permission block.

    ``edit`` is ALWAYS scoped to the worktree path when one is provided,
    preventing the agent from writing files outside its worktree.
    The ``mode`` key (if present) controls auto-approve behviour for
    non-``edit`` tools.
    """
    mode = rules.pop("mode", "")
    base = _allow_all_config(worktree)

    if mode == "auto_approve":
        return base

    for key, val in rules.items():
        if key != "edit":
            base["permission"][key] = val
    return base


class OpenCodeAdapter(CLIAdapter):
    engine = "opencode"

    def write_permission(self, worktree: Path, rules: dict[str, Any]) -> Path:
        dest = worktree / "opencode.json"
        config = _rules_to_permission_config(dict(rules), worktree=worktree)
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
