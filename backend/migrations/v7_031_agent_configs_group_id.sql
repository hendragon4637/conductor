-- v7_031 — agent_configs: add group_id column
-- Required by seed_roster_curation.sql
-- Idempotent.

BEGIN;

ALTER TABLE agent_configs ADD COLUMN IF NOT EXISTS group_id text;

COMMIT;
