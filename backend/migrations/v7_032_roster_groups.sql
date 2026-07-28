-- v7_032 — roster_groups table
-- Required by seed_roster_curation.sql
-- Idempotent.

CREATE TABLE IF NOT EXISTS roster_groups (
    group_id    text PRIMARY KEY,
    description text NOT NULL,
    families    jsonb NOT NULL DEFAULT '[]'::jsonb,
    created_at  timestamptz NOT NULL DEFAULT now()
);
