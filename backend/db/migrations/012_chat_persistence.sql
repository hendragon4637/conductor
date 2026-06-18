-- Conductor v4 — Persistent chat threads + messages.
-- Chat threads and messages survived only in-memory before this migration,
-- causing loss of conversation history on server restart.

CREATE TABLE IF NOT EXISTS chat_threads (
    thread_id   TEXT PRIMARY KEY,
    title       TEXT NOT NULL DEFAULT 'New Chat',
    project_id  TEXT,
    model       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chat_messages (
    message_id      TEXT PRIMARY KEY,
    thread_id       TEXT NOT NULL REFERENCES chat_threads(thread_id) ON DELETE CASCADE,
    role            TEXT NOT NULL,       -- 'user' | 'assistant'
    content         TEXT NOT NULL DEFAULT '',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_chat_messages_thread
    ON chat_messages (thread_id, created_at ASC);
