-- Week 5: Add refresh_tokens table for JWT refresh-token rotation.

CREATE TABLE IF NOT EXISTS refresh_tokens (
    token_hash  TEXT PRIMARY KEY,              -- SHA-256 of the opaque token
    agent_id    TEXT NOT NULL,                 -- who this token belongs to
    expires_at  TIMESTAMPTZ NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    used_at     TIMESTAMPTZ,                   -- NULL = unused, set on rotation
    replaced_by TEXT REFERENCES refresh_tokens(token_hash)  -- chain link
);

CREATE INDEX IF NOT EXISTS idx_refresh_tokens_agent_used
    ON refresh_tokens (agent_id, used_at);
