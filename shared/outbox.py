"""Transactional outbox — reliable event publishing.

Any service that publishes events calls ``emit()`` INSIDE its DB transaction.
A background relay loop publishes pending outbox rows to RabbitMQ.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from contracts.events import ROUTING
from contracts.version import CONTRACTS_VERSION
from shared.models import Outbox, ProcessedEvent

logger = logging.getLogger(__name__)


def emit(session: Session, event: BaseModel) -> None:
    """Write an event to the outbox INSIDE the caller's DB transaction.

    The outbox row is committed atomically with the business write that
    triggered the event.  The background relay publishes it asynchronously.
    """
    cls = type(event)
    key = ROUTING.get(cls)
    if key is None:
        raise ValueError(f"No routing key registered for {cls.__name__}")
    row = Outbox(
        routing_key=key,
        payload=event.model_dump(),
        contracts_version=CONTRACTS_VERSION,
    )
    session.add(row)


def relay_once(session: Session, channel: Any, batch_size: int = 100) -> int:
    """Publish up to ``batch_size`` unpublished outbox rows to RabbitMQ.

    Args:
        session: SQLAlchemy DB session.
        channel: pika channel (or compatible) with a ``basic_publish`` method.
        batch_size: Max rows to publish per call.

    Returns:
        Number of rows published.
    """
    rows = (
        session.query(Outbox)
        .filter(Outbox.published_at.is_(None))
        .order_by(Outbox.created_at)
        .limit(batch_size)
        .all()
    )
    for row in rows:
        channel.basic_publish(
            exchange="conductor.events",
            routing_key=row.routing_key,
            body=json.dumps(row.payload),
            properties=pika_properties(),
        )
        row.published_at = datetime.now(timezone.utc)
    session.commit()
    return len(rows)


def pika_properties():
    """Return persistent delivery-mode pika properties."""
    import pika

    return pika.BasicProperties(delivery_mode=2)


def already_processed(session: Session, consumer: str, event_key: str) -> bool:
    """Check if an event was already processed (idempotency guard)."""
    return (
        session.query(ProcessedEvent)
        .filter(
            ProcessedEvent.consumer == consumer,
            ProcessedEvent.event_key == event_key,
        )
        .first()
        is not None
    )


def mark_processed(session: Session, consumer: str, event_key: str) -> None:
    """Record an event as processed (dedupe)."""
    session.add(ProcessedEvent(consumer=consumer, event_key=event_key))


def dedupe_key(routing_key: str, payload: dict) -> str:
    """Derive a deduplication key from the event payload.

    Uses the first available ID field + routing key.
    """
    for field in ("prev_session_id", "node_session_id", "session_id", "run_id", "plan_id", "agent_config_id"):
        val = payload.get(field)
        if val:
            return f"{val}:{routing_key}"
    return f"{payload.get('ts', '')}:{routing_key}"
