"""EventBus — RabbitMQ topic exchange + per-service queue bindings.

Services use ``declare_topology`` at startup to ensure the exchange and their
queues exist.  ``EventBus`` wraps publish / consume with idempotency and
outbox integration.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable

import pika

from contracts.events import ROUTING
from shared.config import ServiceConfig
from shared.outbox import (
    already_processed,
    dedupe_key,
    mark_processed,
    pika_properties,
    relay_once,
)

logger = logging.getLogger(__name__)


class RequeueHandled(Exception):
    """Raised by a handler to signal that it has already re-queued the event.

    The consumer callback catches this to skip ``mark_processed()``, allowing
    the re-delivered copy to be consumed without deduplication.
    """


EXCHANGE = "conductor.events"

# Queue → routing keys each service consumes
BINDINGS: dict[str, list[str]] = {
    "planner.q": ["run.completed", "run.failed", "node.observed", "run.stopped"],
    "executor.q": ["plan.ratified", "node.steer", "node.remediate", "gate.evaluated", "run.stop"],
    "watcher.q": ["node.spawned"],
    "evaluator.q": ["node.observed", "ratchet.trigger", "calibrate.trigger", "run.completed"],
    "intake.q": ["run.failed", "run.merged", "l4.findings", "plan.awaiting_clarification",
                 "plan.ratifiable", "plan.failed", "plan.rejected", "sys.goal_queued"],
}


def declare_topology(channel: pika.channel.Channel) -> None:
    """Declare the topic exchange + all per-service queues + bindings.

    Idempotent — safe to call on every service startup.
    """
    channel.exchange_declare(EXCHANGE, exchange_type="topic", durable=True)
    for queue, keys in BINDINGS.items():
        channel.queue_declare(queue, durable=True)
        for key in keys:
            channel.queue_bind(queue, EXCHANGE, routing_key=key)

    # Delay queue for evaluator retry with configurable per-message TTL.
    # When the TTL expires, the message is dead-lettered back to
    # conductor.events → routed to evaluator.q for re-consumption.
    channel.queue_declare(
        queue="evaluator.delay",
        durable=True,
        arguments={
            "x-dead-letter-exchange": EXCHANGE,
            "x-dead-letter-routing-key": "node.observed",
        },
    )


class EventBus:
    """High-level event bus over RabbitMQ with outbox-based publishing.

    Usage::

        bus = EventBus(cfg)
        bus.declare()
        bus.publish(event)              # writes to outbox (call inside DB txn)
        bus.start_consumer("planner.q", handler)
        bus.relay_loop()                # background thread
    """

    def __init__(self, cfg: ServiceConfig):
        self._cfg = cfg
        self._connection: pika.BlockingConnection | None = None
        self._channel: pika.channel.Channel | None = None

    # ── Connection ──────────────────────────────────────────────────────

    def connect(self) -> pika.channel.Channel:
        """Open a blocking connection and return the channel."""
        params = pika.URLParameters(self._cfg.rabbit_url)
        # Planner handler can run 2-4 minutes (check-gen LLM calls).
        # Default heartbeat (60s) kills the connection during long handler runs.
        # 600s gives plenty of headroom.
        params.heartbeat = 600
        self._connection = pika.BlockingConnection(params)
        self._channel = self._connection.channel()
        return self._channel

    def close(self) -> None:
        if self._connection and self._connection.is_open:
            self._connection.close()

    # ── Topology ────────────────────────────────────────────────────────

    def declare(self) -> None:
        """Declare exchange + queues on the current channel."""
        if self._channel is None:
            self.connect()
        declare_topology(self._channel)

    # ── Publish (via outbox) ─────────────────────────────────────────────

    # Publishing goes through ``shared.outbox.emit()`` — call inside the
    # service's DB transaction.  The relay loop picks it up asynchronously.

    # ── Consume ──────────────────────────────────────────────────────────

    def start_consumer(
        self,
        queue: str,
        handler: Callable,
        consumer_name: str,
        prefetch: int = 1,
    ) -> None:
        """Start consuming ``queue`` with an idempotent wrapper around ``handler``.

        The handler signature: ``handler(session, event_payload_dict)``
        """
        if self._channel is None:
            self.connect()

        self._channel.basic_qos(prefetch_count=prefetch)

        def _callback(ch, method, properties, body):
            from shared.db import session as db_session

            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                logger.error("Invalid JSON on %s: %s", queue, body[:200])
                ch.basic_nack(method.delivery_tag, requeue=False)
                return

            key = dedupe_key(method.routing_key, payload)
            with db_session() as s:
                if already_processed(s, consumer_name, key):
                    try:
                        ch.basic_ack(method.delivery_tag)
                    except Exception:
                        logger.warning("basic_ack failed (already_processed) — channel may be closed")
                    return
                try:
                    handler(s, payload)
                except RequeueHandled:
                    # Handler saved partial state and published delayed retry.
                    # Ack the original message but skip mark_processed so the
                    # re-delivered copy is not deduplicated.
                    s.commit()
                    logger.debug("RequeueHandled for %s — acked, not marking processed", key)
                except Exception:
                    logger.exception("Handler failed for %s on %s", key, queue)
                    try:
                        ch.basic_nack(method.delivery_tag, requeue=False)
                    except Exception:
                        logger.warning("nack failed too — channel likely dead")
                    return  # keep consumer alive for next message
                else:
                    mark_processed(s, consumer_name, key)
                    s.commit()
            try:
                ch.basic_ack(method.delivery_tag)
            except Exception:
                logger.warning("basic_ack failed after handler success — channel may be closed")

        self._channel.basic_consume(queue, _callback)
        logger.info("Consumer %s started on queue %s", consumer_name, queue)

    def start_consuming(self) -> None:
        """Block and process messages (for service main loops)."""
        if self._channel is None:
            raise RuntimeError("No channel. Call connect() first.")
        self._channel.start_consuming()

    # ── Relay loop ──────────────────────────────────────────────────────

    def relay_loop(self, interval_sec: float = 2.0) -> None:
        """Background loop: publish pending outbox rows.

        Uses its OWN pika connection — the consumer channel is NOT
        thread-safe and sharing it between consumer + relay threads
        corrupts the frame stream (``frame_too_large`` / unexpected
        frame errors on RabbitMQ).

        Reconnects automatically when the channel is closed (e.g.
        RabbitMQ heartbeat timeout).
        """
        from shared.db import session as db_session

        def _open() -> tuple[pika.BlockingConnection, pika.channel.Channel]:
            params = pika.URLParameters(self._cfg.rabbit_url)
            c = pika.BlockingConnection(params)
            ch = c.channel()
            return c, ch

        conn, channel = _open()

        try:
            while True:
                try:
                    if channel.is_closed:
                        logger.warning("Relay channel closed — reconnecting")
                        conn, channel = _open()
                    with db_session() as s:
                        published = relay_once(s, channel)
                        if published:
                            logger.debug("Relayed %d outbox events", published)
                except Exception:
                    logger.exception("Outbox relay error")
                time.sleep(interval_sec)
        finally:
            if conn and conn.is_open:
                conn.close()
