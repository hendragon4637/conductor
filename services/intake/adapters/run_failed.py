from __future__ import annotations

from typing import Any

from backend.planning.store import get_node_sessions, get_run
from services.intake.adapters.base import Answer, GoalIntent, SourceAdapter
from services.intake.adapters.render import render_run_failed

_SEVERITY_RANK = {"fatal": 4, "critical": 3, "error": 2, "warning": 1}
_MIN_SEVERITY = 2


def _severity_rank(severity: str) -> int:
    return _SEVERITY_RANK.get(severity, 0)


class RunFailedAdapter(SourceAdapter):
    """Normalise a run.failed event into an improvement GoalIntent."""

    origin = "run_failed"
    max_attempts = 3

    def normalize(self, payload: dict[str, Any]) -> list[GoalIntent]:
        run_id = payload.get("run_id", "")
        run = get_run(run_id) if run_id else None
        if not run:
            return []

        project_id = run.get("project_id", payload.get("project_id", "default"))
        plan_id = run.get("plan_id", "")
        nodes = get_node_sessions(run_id)
        failed = [
            {
                "node_id": n.get("node_id", ""),
                "gate_outcome": n.get("gate_outcome", "failed"),
                "l2_feedback": n.get("l2_feedback", ""),
                "what": n.get("gate_outcome", ""),
                "where": n.get("node_id", ""),
                "why": n.get("l2_feedback", ""),
            }
            for n in nodes
            if n.get("gate_outcome") in ("fail", "failed")
        ]
        evidence = [f"run:{run_id}", f"plan:{plan_id}"]
        evidence += [f"node:{n['node_id']}" for n in failed if n["node_id"]]

        return [
            GoalIntent(
                origin=self.origin,
                source_ref=f"run:{run_id}",
                project_id=project_id,
                intent_text=render_run_failed(
                    project_id, run_id, plan_id, failed,
                ),
                evidence=evidence,
            )
        ]

    def answer(self, question: str, source_ref: str) -> Answer:
        """Answer clarification using LLM over gate feedback from failed nodes."""
        run_id = source_ref.split(":", 1)[-1] if ":" in source_ref else ""
        run = get_run(run_id) if run_id else None
        if not run:
            return Answer(kind="defer", text="run not found")
        nodes = get_node_sessions(run_id)
        failed = [
            n for n in nodes
            if n.get("gate_outcome") in ("fail", "failed")
        ]
        if not failed:
            return Answer(kind="defer", text="no failed node sessions found")

        # Build context: one block per failed node
        blocks = []
        for n in failed:
            parts = [
                f"node_id: {n.get('node_id', '?')}",
                f"outcome: {n.get('gate_outcome', '?')}",
            ]
            fb = n.get("l2_feedback", "")
            if fb:
                parts.append(f"feedback: {fb}")
            lb = n.get("l1_backtrace", "")
            if lb:
                parts.append(f"backtrace: {lb}")
            blocks.append(" | ".join(parts))

        from services.intake.llm import call_llm

        system = (
            "You are a diagnostic assistant. Given structured gate feedback "
            "from nodes that failed during a run, answer the planner's question "
            "concisely using ONLY the data provided. If the data does not contain "
            "enough information, say exactly: DEFER"
        )
        user = (
            f"Gate feedback for failed nodes:\n"
            f"{chr(10).join(blocks)}\n\n"
            f"Question: {question}"
        )
        reply = call_llm(system, user)
        if not reply or reply.strip().upper() == "DEFER":
            return Answer(kind="defer", text="not available in gate feedback")
        return Answer(kind="answer", text=reply[:2000])
