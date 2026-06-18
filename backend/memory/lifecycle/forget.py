from __future__ import annotations

import datetime

from neo4j import GraphDatabase

from ..graphiti_client import _driver


async def forget(group: str, threshold: float = 0.1, max_age_days: int = 30):
    """
    Decay and archive low-importance, stale session-scope memories.
    Never archives durable memories (type=decision, or durable=true).
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    driver = _driver()
    try:
        with driver.session() as s:
            candidates = list(
                s.run(
                    """
                    MATCH (m:MemoryFact)
                    WHERE m.group_id = $group
                      AND (m.archived IS NULL OR m.archived = false)
                      AND (m.durable IS NULL OR m.durable = false)
                      AND (m.type IS NULL OR m.type <> 'decision')
                      AND (m.importance IS NULL OR m.importance < $threshold)
                      AND (m.last_used_at IS NULL
                           OR m.last_used_at < datetime() - duration({days: $days}))
                    RETURN m.uuid AS uuid, m.text AS text, m.importance AS importance,
                           m.last_used_at AS last_used_at, m.type AS type
                    """,
                    group=group,
                    threshold=threshold,
                    days=max_age_days,
                )
            )

            archived = 0
            for row in candidates:
                s.run(
                    "MATCH (m:MemoryFact {uuid: $uuid}) SET m.archived = true, m.archived_at = datetime()",
                    uuid=row["uuid"],
                )
                archived += 1

        return {"archived": archived, "group": group}
    finally:
        driver.close()
