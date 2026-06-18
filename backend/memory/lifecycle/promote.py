from __future__ import annotations

import uuid as uuid_mod

from neo4j import GraphDatabase

from ..graphiti_client import _driver, search_memory
from ..scopes import group_id


async def propose_promotion(memory_id: str, from_scope: str, to_scope: str):
    """
    Create a promotion proposal for human approval.
    Does NOT auto-apply — queues it as a PromotionProposal node.
    """
    proposal_id = str(uuid_mod.uuid4())
    driver = _driver()
    try:
        with driver.session() as s:
            s.run(
                """
                CREATE (p:PromotionProposal {
                    uuid: $uuid,
                    memory_uuid: $memory_id,
                    from_scope: $from_scope,
                    to_scope: $to_scope,
                    status: 'pending',
                    created_at: datetime()
                })
                """,
                uuid=proposal_id,
                memory_id=memory_id,
                from_scope=from_scope,
                to_scope=to_scope,
            )
        return proposal_id
    finally:
        driver.close()


async def approve_promotion(proposal_id: str):
    """
    Human approves: re-scope the memory to to_scope, mark durable.
    """
    driver = _driver()
    try:
        with driver.session() as s:
            proposal = s.run(
                "MATCH (p:PromotionProposal {uuid: $uuid}) RETURN p",
                uuid=proposal_id,
            ).single()
            if not proposal:
                raise ValueError(f"proposal {proposal_id} not found")
            p = proposal["p"]
            s.run(
                """
                MATCH (m:MemoryFact {uuid: $memory_uuid})
                SET m.group_id = $to_scope, m.durable = true
                """,
                memory_uuid=p.get("memory_uuid"),
                to_scope=p.get("to_scope"),
            )
            s.run(
                "MATCH (p:PromotionProposal {uuid: $uuid}) SET p.status = 'approved'",
                uuid=proposal_id,
            )
    finally:
        driver.close()


async def reject_promotion(proposal_id: str):
    driver = _driver()
    try:
        with driver.session() as s:
            s.run(
                "MATCH (p:PromotionProposal {uuid: $uuid}) SET p.status = 'rejected'",
                uuid=proposal_id,
            )
    finally:
        driver.close()


async def list_pending_promotions():
    driver = _driver()
    try:
        with driver.session() as s:
            return list(
                s.run(
                    """
                    MATCH (p:PromotionProposal)
                    WHERE p.status = 'pending'
                    RETURN p.uuid AS uuid, p.memory_uuid AS memory_uuid,
                           p.from_scope AS from_scope, p.to_scope AS to_scope,
                           p.created_at AS created_at
                    """
                )
            )
    finally:
        driver.close()
