-- v5_010_outbox.sql
-- Transactional outbox + processed-events dedupe tables.
--
-- Part of the services decomposition (File 01): any service that publishes
-- events writes an outbox row in the same txn as its business write; a
-- background relay publishes to RabbitMQ.
--
-- Run:  docker exec -i postgres psql -U aipc -d aipc_conductor < migrations/v5_010_outbox.sql

CREATE TABLE IF NOT EXISTS outbox (
    id              BIGSERIAL PRIMARY KEY,
    routing_key     TEXT NOT NULL,
    payload         JSONB NOT NULL,
    contracts_version TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    published_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_outbox_unpublished
    ON outbox (created_at)
    WHERE published_at IS NULL;

-- Idempotency: consumers dedupe processed events so at-least-once delivery
-- is safe.
CREATE TABLE IF NOT EXISTS processed_events (
    consumer    TEXT NOT NULL,
    event_key   TEXT NOT NULL,       -- e.g. "ns_abc123:node.observed"
    processed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (consumer, event_key)
);
