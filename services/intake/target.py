from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def resolve_target(
    source_ref: str | None,
    project_id: str | None,
) -> dict[str, Any]:
    """Resolve the intake target for an event, with system-awareness.

    When a finding comes from a system-level L4 run (source_ref starts with
    ``"l4:"`` and the project is a system), this function resolves all
    component projects belonging to the system so the finding can be routed.

    Args:
        source_ref: Event lineage pointer (e.g. ``"l4:<run_id>"``).
        project_id: Suspected project_id from the event payload.

    Returns:
        Dict with:
        - ``project_id``: The target project (system or component).
        - ``system_id``: The system, if project is part of one.
        - ``component_ids``: All component project IDs in the system.
        - ``kind``: ``"system"`` or ``"component"`` or ``"standalone"``.
    """
    import psycopg

    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url or not project_id:
        return {
            "project_id": project_id or "default",
            "system_id": None,
            "component_ids": [],
            "kind": "standalone",
        }

    try:
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                # Is this project itself a system?
                cur.execute(
                    """SELECT system_id, kind FROM projects
                       WHERE project_id = %s""",
                    (project_id,),
                )
                row = cur.fetchone()
                if not row:
                    return {
                        "project_id": project_id,
                        "system_id": None,
                        "component_ids": [],
                        "kind": "standalone",
                    }

                db_system_id, kind = row

                if db_system_id:
                    # This is a component of a system — resolve the system
                    system_id = db_system_id
                    cur.execute(
                        """SELECT project_id FROM projects
                           WHERE system_id = %s""",
                        (system_id,),
                    )
                    components = [r[0] for r in cur.fetchall()]
                    return {
                        "project_id": project_id,
                        "system_id": system_id,
                        "component_ids": components,
                        "kind": "component",
                    }

                if kind == "system":
                    # This IS the system — list its components
                    cur.execute(
                        """SELECT project_id FROM projects
                           WHERE system_id = %s""",
                        (project_id,),
                    )
                    components = [r[0] for r in cur.fetchall()]
                    return {
                        "project_id": project_id,
                        "system_id": project_id,
                        "component_ids": components,
                        "kind": "system",
                    }

                # Standalone project (no system involvement)
                return {
                    "project_id": project_id,
                    "system_id": None,
                    "component_ids": [],
                    "kind": "standalone",
                }
    except Exception as exc:
        logger.warning("resolve_target failed for project=%s: %s", project_id, exc)
        return {
            "project_id": project_id or "default",
            "system_id": None,
            "component_ids": [],
            "kind": "standalone",
            "error": str(exc)[:200],
        }
