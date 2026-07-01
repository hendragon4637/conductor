-- v6_010_capabilities.sql — Capability registry (File 01)
-- Each row describes a competency with quality dimensions (objective→L1, subjective→L2),
-- required_tools for realizability, and golden_ref_count for confidence.
BEGIN;

CREATE TABLE IF NOT EXISTS capabilities (
  name              TEXT PRIMARY KEY,
  family            TEXT NOT NULL,               -- software|data|media|creative|business|research
  description       TEXT NOT NULL,
  quality_dimensions JSONB NOT NULL,             -- [{id, dimension, kind: 'objective'|'subjective'}]
  required_tools    JSONB NOT NULL DEFAULT '[]'::jsonb,
  golden_ref_count  INTEGER NOT NULL DEFAULT 0,
  source            TEXT NOT NULL DEFAULT 'example-generated',
  version           INTEGER NOT NULL DEFAULT 1,
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_cap_family ON capabilities(family);

COMMIT;
