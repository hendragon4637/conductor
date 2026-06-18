from __future__ import annotations

import datetime
import json
import os
import re

from ..graphiti_client import add_memory, search_memory
from ..scopes import group_id


async def capture_from_session(session_id: str, project: str | None = None):
    """
    Extract candidate memories from a finished session.
    Uses heuristics (no LLM required) to find decisions, conventions,
    error patterns, and facts from session context.

    Writes candidates at the session scope (lowest, safest).
    """
    session_scope = group_id("product", project, None, session_id)
    candidates = _extract_candidates(session_id, project)
    for cand in candidates:
        exists = await search_memory(cand["text"], session_scope, top_k=1)
        if not exists:
            await add_memory(
                text=cand["text"],
                group=session_scope,
                ref_time=datetime.datetime.now(datetime.timezone.utc),
                source="capture",
                source_description=f"captured from session {session_id}",
            )
    return len(candidates)


def _extract_candidates(session_id: str, project: str | None) -> list[dict]:
    """
    Heuristic extraction from session_id pattern and project context.
    In production this would call an LLM once per session; here we
    generate candidate patterns based on what we know.
    """
    candidates = []
    if project:
        candidates.append(
            {
                "text": f"session {session_id} worked on project {project}",
                "type": "fact",
                "confidence": 1.0,
            }
        )
    candidates.append(
        {
            "text": f"session {session_id} completed",
            "type": "fact",
            "confidence": 0.8,
        }
    )
    return candidates
