-- File 09 — Enriched agent_config with co-located L1/L2 default_checks
BEGIN;

ALTER TABLE agent_configs ADD COLUMN IF NOT EXISTS default_checks JSONB NOT NULL DEFAULT '{}'::jsonb;

COMMIT;
