from __future__ import annotations

from typing import Any

from services.intake.adapters.base import Answer, GoalIntent, SourceAdapter


class SystemGoalAdapter(SourceAdapter):
    """Normalise sys.goal_queued events and answer clarifications.

    ``normalize`` produces one ``GoalIntent`` per project-level goal
    generated from a system decomposition.

    ``answer`` loads the project goal + parent system context (system
    name, description, glossary) and auto-generates a clarification
    answer via LLM, keeping the project informed by the system intent.
    """

    origin = "system_goal"
    max_attempts = 3

    def normalize(self, payload: dict[str, Any]) -> list[GoalIntent]:
        project_id = payload.get("project_id", "")
        raw_input = payload.get("raw_input", "")
        if not project_id or not raw_input:
            return []
        return [
            GoalIntent(
                origin=self.origin,
                source_ref=f"sys:{project_id}",
                project_id=project_id,
                intent_text=raw_input,
            )
        ]

    def answer(self, question: str, source_ref: str) -> Answer:
        from services.intake.store import load_intent_by_source_ref

        row = load_intent_by_source_ref(source_ref)
        if not row:
            return Answer(kind="defer", text="intent not found")

        project_goal = row.get("intent_text", "")
        project_id = source_ref.split(":", 1)[-1] if ":" in source_ref else ""

        system_context = ""
        if project_id:
            system_context = _fetch_system_context(project_id)

        from services.intake.llm import call_llm

        system = (
            "You answer clarification questions for a project-level goal that "
            "was auto-generated as part of a larger system decomposition. "
            "Answer concisely and specifically to keep planning moving forward. "
            "Use the system context below to inform your answer. "
            "If the question cannot be answered from the available context, "
            "say exactly: DEFER"
        )
        user = (
            f"Project goal: {project_goal}\n\n"
            f"System context:\n{system_context or '(none — standalone project)'}\n\n"
            f"Question: {question}"
        )
        reply = call_llm(system, user)
        if not reply or reply.strip().upper() == "DEFER":
            return Answer(kind="defer", text="not available in system context")
        return Answer(kind="answer", text=reply[:2000])


def _fetch_system_context(project_id: str) -> str:
    """Return system name, description, and glossary for *project_id*."""
    import json as _json
    import os

    import psycopg

    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        return ""
    try:
        with psycopg.connect(db_url) as c:
            with c.cursor() as cur:
                cur.execute(
                    """SELECT s.name, s.description, s.glossary, s.system_id
                       FROM systems s
                       JOIN projects p ON p.system_id = s.system_id
                       WHERE p.project_id = %s""",
                    (project_id,),
                )
                row = cur.fetchone()
        if not row:
            return ""
        parts = [f"System: {row[0]}"]
        if row[1]:
            parts.append(f"Description: {row[1]}")
        if row[2]:
            gloss = row[2]
            if isinstance(gloss, str):
                try:
                    gloss = _json.loads(gloss)
                except (_json.JSONDecodeError, TypeError):
                    gloss = {}
            if isinstance(gloss, dict) and gloss:
                terms = "; ".join(f"{k}: {v}" for k, v in gloss.items())
                parts.append(f"Glossary: {terms}")
        return "\n".join(parts)
    except Exception:
        import logging
        logging.getLogger(__name__).exception(
            "Failed to fetch system context for %s", project_id
        )
        return ""
