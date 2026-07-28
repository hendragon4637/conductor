from __future__ import annotations

from services.intake.adapters.base import Answer, GoalIntent, SourceAdapter
from services.intake.adapters.render import render_l4

_SEVERITY_RANK = {"high": 4, "medium": 2, "low": 1}
_MIN_SEVERITY = 2


def _severity_rank(severity: str) -> int:
    return _SEVERITY_RANK.get(severity, 0)


class L4FindingsAdapter(SourceAdapter):
    """Normalise an l4.findings event into an improvement GoalIntent.

    Filters findings by severity (≥ `_MIN_SEVERITY`). Produces ONE goal
    per run — the planner decomposes; intake does not.
    """

    origin = "l4_findings"
    max_attempts = 3

    def normalize(self, payload: dict) -> list[GoalIntent]:
        findings = payload.get("findings", [])
        project_id = payload.get("project_id", "default")
        run_id = payload.get("run_id", "")

        keep = [f for f in findings
                if _severity_rank(f.get("severity", "")) >= _MIN_SEVERITY]
        if not keep:
            return []

        # Deduplicate where paths across findings
        seen: set[str] = set()
        evidence: list[str] = [f"run:{run_id}"]
        for f in keep:
            for w in f.get("where", []):
                if w not in seen:
                    seen.add(w)
                    evidence.append(w)

        return [
            GoalIntent(
                origin=self.origin,
                source_ref=f"l4:{run_id}" if run_id else "l4:unknown",
                project_id=project_id,
                intent_text=render_l4(project_id, run_id, keep),
                evidence=evidence,
            )
        ]

    def answer(self, question: str, source_ref: str) -> Answer:
        """Answer clarification using LLM over the stored L4 findings text."""
        from services.intake.llm import call_llm
        from services.intake.store import load_intent_by_source_ref

        # Load the stored intent to get the rendered findings text
        row = load_intent_by_source_ref(source_ref)
        context = (row or {}).get("intent_text", "")

        if not context:
            return Answer(kind="defer", text="findings context not available")

        system = (
            "You are a diagnostic assistant. Given L4 usage findings for a "
            "product, answer the planner's question concisely using ONLY the "
            "findings data provided. If the data does not contain enough "
            "information, say exactly: DEFER"
        )
        user = (
            f"L4 Usage findings:\n{context}\n\n"
            f"Question: {question}"
        )
        reply = call_llm(system, user)
        if not reply or reply.strip().upper() == "DEFER":
            return Answer(kind="defer", text="not available in findings")
        return Answer(kind="answer", text=reply[:2000])
