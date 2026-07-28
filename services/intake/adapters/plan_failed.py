from __future__ import annotations

from services.intake.adapters.base import Answer, GoalIntent
from services.intake.adapters.render import render_reformulation
from services.intake.store import load_intent_by_plan


class PlanFailedAdapter:
    """Reformulate after a plan gate failure (plan was malformed).

    Cap of 3 attempts — after that the intent escalates.
    """

    origin = "plan_failed"
    max_attempts = 3

    def normalize(self, payload: dict) -> list[GoalIntent]:
        plan_id = payload.get("plan_id", "")
        prev = load_intent_by_plan(plan_id)
        if not prev:
            return []
        note = payload.get("error", "gate failure")
        attempt = (prev.get("attempt") or 1) + 1
        return [
            GoalIntent(
                origin=self.origin,
                source_ref=prev.get("source_ref") or "",
                project_id=prev.get("project_id", "default"),
                intent_text=render_reformulation(
                    prev.get("intent_text", ""), note, attempt, self.origin,
                ),
                evidence=[*prev.get("evidence", []), f"plan:{plan_id}"],
                attempt=attempt,
            )
        ]

    def answer(self, question: str, source_ref: str) -> Answer:
        return Answer(kind="defer", text="reformulation — human clarification needed")
