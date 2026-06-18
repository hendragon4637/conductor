-- v4_021_v5_1_schema_delta.sql
-- Completes the v5.1 schema migration that v4_020 attempted but never applied fully.
-- v4_010 created runs/node_sessions with the OLD schema (no members, gate_mode, aionui fields).
-- This delta adds what's missing and reconciles to the v5.1 E2E spec.
-- Idempotent via IF NOT EXISTS / IF EXISTS.

BEGIN;

-- ============ PLANS: drop session_id, add goal + success (normalized object) ============
ALTER TABLE plans DROP COLUMN IF EXISTS session_id;
ALTER TABLE plans ADD COLUMN IF NOT EXISTS goal TEXT;
ALTER TABLE plans ADD COLUMN IF NOT EXISTS success JSONB NOT NULL DEFAULT '{}'::jsonb;

-- ============ NODE_SESSIONS: add v5.1 columns that v4_020's CREATE TABLE IF NOT EXISTS missed ============
ALTER TABLE node_sessions ADD COLUMN IF NOT EXISTS members JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE node_sessions ADD COLUMN IF NOT EXISTS gate_mode TEXT NOT NULL DEFAULT 'l1_l2';
ALTER TABLE node_sessions ADD COLUMN IF NOT EXISTS aionui_team_id TEXT;
ALTER TABLE node_sessions ADD COLUMN IF NOT EXISTS aionui_conversation_id TEXT;
ALTER TABLE node_sessions ADD COLUMN IF NOT EXISTS langfuse_trace_id TEXT;

-- ============ TRACES: add node_session_id FK (replacing task_id usage) ============
ALTER TABLE traces ADD COLUMN IF NOT EXISTS node_session_id TEXT REFERENCES node_sessions(id);
CREATE INDEX IF NOT EXISTS idx_traces_node_session ON traces(node_session_id);

-- ============ SESSION_SIGNALS: add node_session_id FK ============
ALTER TABLE session_signals ADD COLUMN IF NOT EXISTS node_session_id TEXT REFERENCES node_sessions(id);
CREATE INDEX IF NOT EXISTS idx_ss_node_session ON session_signals(node_session_id);

-- ============ JUDGE_TRUST: gates the ratchet (missing from DB entirely) ============
CREATE TABLE IF NOT EXISTS judge_trust (
  node_type     TEXT PRIMARY KEY,
  agreement     REAL,
  mae           REAL,
  trusted       BOOLEAN NOT NULL DEFAULT FALSE,
  calibrated_at TIMESTAMPTZ DEFAULT now()
);

-- ============ GOLDEN_SET: ensure it has the v5.1 columns (existing table from v4_006 has a different schema) ============
ALTER TABLE golden_set ADD COLUMN IF NOT EXISTS task TEXT;
ALTER TABLE golden_set ADD COLUMN IF NOT EXISTS artifact_blob TEXT;
ALTER TABLE golden_set ADD COLUMN IF NOT EXISTS split TEXT NOT NULL DEFAULT 'calibration';

COMMIT;
