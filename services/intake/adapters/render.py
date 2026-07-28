from __future__ import annotations


def render_run_failed(project_id: str, run_id: str, plan_id: str,
                      failed_nodes: list[dict]) -> str:
    """Render intent text from a run.failed event.

    Each failed node carries 'node_id', 'what', 'where', 'why' keys from
    gate feedback.
    """
    parts = [
        f"Fix the failure in {project_id}. The previous run {run_id} failed at:",
    ]
    for n in failed_nodes:
        node_id = n.get("node_id", "?")
        what = n.get("what", n.get("gate_outcome", "unknown"))
        where = n.get("where", "")
        why = n.get("why", n.get("l2_feedback", ""))
        line = f"  - {node_id}: {what}"
        if where:
            line += f" at {where}"
        if why:
            line += f" — {why}"
        parts.append(line)
    parts.append(
        "Produce a plan that resolves these failures and passes the project's gates."
    )
    return "\n".join(parts)


def render_l4(project_id: str, run_id: str, findings: list[dict]) -> str:
    """Render intent text from an l4.findings event."""
    parts = [f"Improve {project_id} based on usage findings."]
    for i, f in enumerate(findings, 1):
        what = f.get("what", "")
        where = ", ".join(f.get("where", []))
        why = f.get("why", "")
        line = f"  Finding {i}: {what}"
        if where:
            line += f" at [{where}]"
        if why:
            line += f" — {why}"
        parts.append(line)
    return "\n".join(parts)


def render_reformulation(prev_text: str, note: str, attempt: int,
                         origin: str) -> str:
    """Render reformulated intent after plan failure or rejection.

    Text differs by origin — plan_failed (malformed) vs ratify_rejected (refused).
    """
    if origin == "ratify_rejected":
        guidance = (
            f"NOTE — attempt {attempt - 1} produced a valid plan that was "
            f"REJECTED: {note}. The approach was refused, not malformed. "
            "Propose a materially DIFFERENT approach."
        )
    else:
        guidance = (
            f"NOTE — attempt {attempt - 1} produced a plan that FAILED ITS "
            f"GATE: {note}. Restate scope more precisely; do not repeat the "
            "same plan shape."
        )
    return f"{prev_text}\n\n{guidance}"


def render_feedback(project_id: str, findings: list[dict]) -> str:
    """Render intent text from human feedback."""
    parts = [f"Human feedback improvement for {project_id}:"]
    for i, f in enumerate(findings, 1):
        what = f.get("what", "")
        where = ", ".join(f.get("where", []))
        why = f.get("why", "")
        line = f"  {i}. {what}"
        if where:
            line += f" at [{where}]"
        if why:
            line += f" — {why}"
        parts.append(line)
    return "\n".join(parts)
