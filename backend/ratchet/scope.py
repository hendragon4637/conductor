"""Scope detection — determines whether a mutation is global or project-scoped.

Scope gating policy (File 04):
- Global-scope winning mutation → QUEUE for human approval (never auto-apply).
- Project-scope winning mutation → may auto-apply.

Scope is inferred from the agent_config's ``domain`` field:
- Domains ``backend`` and ``general`` are system-level (global).
- All other domains are project-scoped.
"""
from __future__ import annotations

import os
from typing import Literal

Scope = Literal["global", "project"]


def detect_scope(agent_config_id: str) -> Scope:
    """Return the scope for a given agent config.

    Args:
        agent_config_id: The agent config identifier
                        (e.g. ``"opencode:backend-executor"`` or ``"badminton-executor"``).

    Returns:
        ``"global"`` for system-level configs, ``"project"`` otherwise.
    """
    domain = _resolve_domain(agent_config_id)
    if domain in ("backend", "general"):
        return "global"
    return "project"


def _resolve_domain(agent_config_id: str) -> str:
    """Query the agent_configs table for the domain of the given config id.

    Falls back to heuristic parsing if the DB is unavailable:
    ``opencode:backend-*`` and ``orchestrator`` → ``backend``/``general``;
    everything else → the prefix before ``-`` or the full id.
    """
    db_url = os.environ.get("DATABASE_URL", "")
    if db_url:
        try:
            import psycopg
            from psycopg.rows import dict_row

            with psycopg.connect(db_url, row_factory=dict_row) as c:
                with c.cursor() as cur:
                    cur.execute(
                        "SELECT domain FROM agent_configs WHERE agent_config_id = %s",
                        (agent_config_id,),
                    )
                    row = cur.fetchone()
                    if row:
                        return row["domain"]
        except Exception:
            pass

    # Fallback heuristic
    if agent_config_id.startswith("opencode:") or agent_config_id == "orchestrator":
        return "backend"
    prefix = agent_config_id.split("-")[0]
    return prefix
