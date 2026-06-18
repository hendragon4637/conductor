from pathlib import Path

from .base import CLIAdapter


class GeminiAdapter(CLIAdapter):
    engine = "gemini"

    def write_permission(self, worktree: Path, rules: dict) -> Path:
        # TODO: Gemini permission model varies; store as a note for now
        dest = worktree / ".gemini" / "permissions.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        import json
        dest.write_text(json.dumps(rules, indent=2))
        return dest

    def write_instructions(self, worktree: Path, content: str) -> Path:
        dest = worktree / "GEMINI.md"
        dest.write_text(content)
        return dest

    def write_skills(self, worktree: Path, skills: dict[str, str]) -> list[Path]:
        written = []
        for name, markdown in skills.items():
            dest = worktree / ".gemini" / "skills" / name / "SKILL.md"
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(markdown)
            written.append(dest)
        return written

    def aionui_preset_agent_type(self) -> str:
        return "acp"
