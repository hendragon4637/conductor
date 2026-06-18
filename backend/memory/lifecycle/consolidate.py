from __future__ import annotations

import datetime
import hashlib
import re

from neo4j import GraphDatabase

from ..graphiti_client import _driver, search_memory, update_memory
from ..scopes import group_id


async def consolidate(group: str):
    """
    Dedup near-duplicate memories in a group, update importance scores.
    """
    driver = _driver()
    try:
        with driver.session() as s:
            rows = list(
                s.run(
                    """
                    MATCH (m:MemoryFact)
                    WHERE m.group_id = $group AND (m.archived IS NULL OR m.archived = false)
                    RETURN m.uuid AS uuid, m.text AS text,
                           m.importance AS importance, m.use_count AS use_count,
                           m.last_used_at AS last_used_at
                    ORDER BY m.ref_time DESC
                    """,
                    group=group,
                )
            )

        seen: dict[str, list] = {}
        dups: list[str] = []
        for r in rows:
            sig = _text_signature(r["text"])
            if sig in seen:
                primary = seen[sig][0]
                dup = r["uuid"]
                dups.append(dup)
                _merge_into_primary(primary, dup, r)
            else:
                seen[sig] = [r]

        now = datetime.datetime.now(datetime.timezone.utc)
        for records in seen.values():
            r = records[0]
            days_since_use = (
                (now - r["last_used_at"]).total_seconds() / 86400
                if r.get("last_used_at")
                else 30
            )
            importance = min(
                1.0,
                (r.get("importance") or 0.5)
                * (1.1 ** (r.get("use_count") or 0))
                * (0.95**days_since_use),
            )
            await update_memory(
                r["uuid"],
                importance=round(importance, 3),
                use_count=(r.get("use_count") or 0) + 1,
                last_used_at=now,
            )

        return {"survivors": len(seen), "duplicates_removed": len(dups)}
    finally:
        driver.close()


def _text_signature(text: str) -> str:
    """Fuzzy signature: lowercase, strip punctuation, normalize whitespace."""
    norm = re.sub(r"[^a-z0-9\s]", "", text.lower())
    norm = re.sub(r"\s+", " ", norm).strip()
    return hashlib.md5(norm.encode()).hexdigest()


def _merge_into_primary(primary: dict, dup_uuid: str, dup_row: dict):
    """Transfer use_count from dup to primary, then delete dup."""
    with _driver() as d, d.session() as s:
        s.run(
            "MATCH (d:MemoryFact {uuid: $dup}) SET d.archived = true, d.duplicate_of = $primary",
            dup=dup_uuid,
            primary=primary["uuid"],
        )
