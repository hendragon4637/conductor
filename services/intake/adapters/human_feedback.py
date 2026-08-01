from __future__ import annotations

import time
from typing import Any

from services.intake.adapters.base import Answer, GoalIntent, SourceAdapter
from services.intake.adapters.render import render_feedback


def _ts() -> str:
    return str(int(time.time()))


class HumanFeedbackAdapter(SourceAdapter):
    """Normalise POST /intake/feedback body into a GoalIntent.

    Always defers clarification questions back to the human.
    """

    origin = "human_feedback"
    max_attempts = 3

    def normalize(self, payload: dict[str, Any]) -> list[GoalIntent]:
        project_id = payload.get("project_id", "default")
        findings = payload.get("findings", [])

        evidence = list({
            w for f in findings for w in f.get("where", [])
        })

        return [
            GoalIntent(
                origin=self.origin,
                source_ref=f"human:{_ts()}",
                project_id=project_id,
                intent_text=render_feedback(project_id, findings),
                evidence=evidence,
            )
        ]

    def answer(self, question: str, source_ref: str) -> Answer:
        return Answer(kind="defer", text="human-sourced — ask the human")
