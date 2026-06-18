from __future__ import annotations

import datetime
import os
import uuid

from neo4j import GraphDatabase


def _driver():
    return GraphDatabase.driver(
        os.environ["NEO4J_URI"],
        auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]),
    )


async def init_indices():
    with _driver() as d, d.session() as s:
        s.run(
            "CREATE CONSTRAINT memory_fact_uuid IF NOT EXISTS "
            "FOR (m:MemoryFact) REQUIRE m.uuid IS UNIQUE"
        )
        s.run(
            "CREATE INDEX memory_fact_group IF NOT EXISTS "
            "FOR (m:MemoryFact) ON (m.group_id)"
        )
        s.run(
            "CREATE INDEX memory_fact_ref_time IF NOT EXISTS "
            "FOR (m:MemoryFact) ON (m.ref_time)"
        )
    print("init_indices() completed")


async def add_memory(
    text: str,
    group: str,
    ref_time: datetime.datetime | None = None,
    source: str = "conductor",
    source_description: str = "",
):
    with _driver() as d, d.session() as s:
        s.run(
            """
            CREATE (m:MemoryFact {
                uuid: $uuid,
                group_id: $group,
                text: $text,
                source: $source,
                source_description: $source_description,
                ref_time: $ref_time,
                created_at: datetime()
            })
            """,
            uuid=str(uuid.uuid4()),
            group=group,
            text=text,
            source=source,
            source_description=source_description or source,
            ref_time=ref_time or datetime.datetime.now(datetime.timezone.utc),
        )


async def init_lifecycle_schema():
    with _driver() as d, d.session() as s:
        s.run(
            "CREATE INDEX memory_fact_type IF NOT EXISTS "
            "FOR (m:MemoryFact) ON (m.type)"
        )
        s.run(
            "CREATE INDEX memory_fact_importance IF NOT EXISTS "
            "FOR (m:MemoryFact) ON (m.importance)"
        )
        s.run(
            "CREATE INDEX memory_fact_archived IF NOT EXISTS "
            "FOR (m:MemoryFact) ON (m.archived)"
        )
        s.run(
            "CREATE CONSTRAINT proposal_uuid IF NOT EXISTS "
            "FOR (p:PromotionProposal) REQUIRE p.uuid IS UNIQUE"
        )
    print("init_lifecycle_schema() completed")


async def update_memory(uuid_str: str, **props):
    sets = ", ".join(f"m.{k} = ${k}" for k in props)
    params = {"uuid": uuid_str, **props}
    with _driver() as d, d.session() as s:
        s.run(f"MATCH (m:MemoryFact {{uuid: $uuid}}) SET {sets}", **params)


async def search_memory(query: str, group: str, top_k: int = 8):
    with _driver() as d, d.session() as s:
        result = s.run(
            """
            MATCH (m:MemoryFact)
            WHERE m.group_id = $group
              AND toLower(m.text) CONTAINS toLower($search_term)
            RETURN m.uuid AS uuid, m.text AS fact, m.group_id AS group_id,
                   m.source AS source, m.ref_time AS ref_time, m.created_at AS created_at
            ORDER BY m.ref_time DESC
            LIMIT $top_k
            """,
            group=group,
            search_term=query,
            top_k=top_k,
        )
        return list(result)
