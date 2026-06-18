from abc import ABC, abstractmethod
from pathlib import Path


class CLIAdapter(ABC):
    engine: str

    @abstractmethod
    def write_permission(self, worktree: Path, rules: dict) -> Path:
        ...

    @abstractmethod
    def write_instructions(self, worktree: Path, content: str) -> Path:
        ...

    @abstractmethod
    def write_skills(self, worktree: Path, skills: dict[str, str]) -> list[Path]:
        ...

    @abstractmethod
    def aionui_preset_agent_type(self) -> str:
        ...

    def aionui_assistant_id(self) -> str | None:
        """AionUi assistant_id for the agent (None = let AionUi select by preset type)."""
        return None
