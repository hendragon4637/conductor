-- Conductor v4 — Session signals table for watcher polling.
-- Stores deterministic signal snapshots derived from the 3-source pipeline.
-- The watcher (File 16) polls this table every interval; it is NOT written
-- by any LLM — only by the observability ingestion pipeline (File 15).

CREATE TABLE IF NOT EXISTS session_signals (
    id          BIGSERIAL PRIMARY KEY,
    session_id  TEXT NOT NULL,
    ts          TIMESTAMPTZ NOT NULL DEFAULT now(),
    token_rate  NUMERIC DEFAULT 0,
    last_activity TIMESTAMPTZ,
    terminal    BOOLEAN DEFAULT false,
    quota_suspected BOOLEAN DEFAULT false,
    pid_alive   BOOLEAN DEFAULT false,
    fs_changed  BOOLEAN DEFAULT false
);

CREATE INDEX IF NOT EXISTS idx_session_signals_session_ts
    ON session_signals (session_id, ts DESC);
