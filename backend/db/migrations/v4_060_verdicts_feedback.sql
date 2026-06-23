-- File 09 — Verdicts + feedback + delta tracking
-- Adds columns to node_sessions for per-layer verdicts, feedback, and gate outcome.
BEGIN;

ALTER TABLE node_sessions ADD COLUMN IF NOT EXISTS l1_flagged     BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE node_sessions ADD COLUMN IF NOT EXISTS l1_feedback    JSONB;
ALTER TABLE node_sessions ADD COLUMN IF NOT EXISTS l1_passed_ids  JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE node_sessions ADD COLUMN IF NOT EXISTS l2_passed      BOOLEAN;
ALTER TABLE node_sessions ADD COLUMN IF NOT EXISTS l2_score       REAL;
ALTER TABLE node_sessions ADD COLUMN IF NOT EXISTS l2_feedback    JSONB;
ALTER TABLE node_sessions ADD COLUMN IF NOT EXISTS gate_outcome   TEXT;

COMMIT;
