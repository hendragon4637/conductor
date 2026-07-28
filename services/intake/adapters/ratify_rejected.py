from __future__ import annotations

from services.intake.adapters.base import Answer, GoalIntent
from services.intake.adapters.render import render_reformulation
from services.intake.store import load_intent_by_plan


class RatifyRejectedAdapter:
    """Reformulate after a plan rejection (plan was well-formed but refused).

    Cap of 2 attempts — repeated refusal means the intent itself is wrong.
    Adapter produces materially different reformulation text than PlanFailedAdapter.
    """

    origin = "ratify_rejected"
    max_attempts = 2

    def normalize(self, payload: dict) -> list[GoalIntent]:
        plan_id = payload.get("plan_id", "")
        prev = load_intent_by_plan(plan_id)
        if not prev:
            return []
        note = payload.get("reason", payload.get("error", "rejected"))
        rejected_by = payload.get("rejected_by", "policy")
        detail = f"{note} (rejected_by={rejected_by})"
        attempt = (prev.get("attempt") or 1) + 1
        return [
            GoalIntent(
                origin=self.origin,
                source_ref=prev.get("source_ref") or "",
                project_id=prev.get("project_id", "default"),
                intent_text=render_reformulation(
                    prev.get("intent_text", ""), detail, attempt, self.origin,
                ),
                evidence=[*prev.get("evidence", []), f"plan:{plan_id}"],
                attempt=attempt,
            )
        ]

    def answer(self, question: str, source_ref: str) -> Answer:
        return Answer(kind="defer", text="rejection — human clarification needed")
