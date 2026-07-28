from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel


class GoalIntent(BaseModel):
    """Normalised goal that every adapter produces — planner needs no source-specific knowledge."""

    origin: str
    source_ref: str
    project_id: str
    intent_text: str
    evidence: list[str] = []
    attempt: int = 1


class Answer(BaseModel):
    """Structured reply for clarification questions."""

    kind: Literal["answer", "defer"]
    text: str | None = None


class SourceAdapter(Protocol):
    """Protocol every source adapter implements."""

    origin: str
    max_attempts: int = 3

    def normalize(self, payload: dict) -> list[GoalIntent]:
        """Convert a source event into one or more GoalIntents."""

    def answer(self, question: str, source_ref: str) -> Answer:
        """Answer a clarification question from available source context, or defer."""
