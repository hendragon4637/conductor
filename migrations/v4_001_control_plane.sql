-- Conductor v4 — Control Plane Schema Migration
-- Database: aipc_conductor
-- Applied: 2026-05-29

-- ============================================================================
-- STEP 1: Retire week-1 tables (rename, never drop)
-- ============================================================================

DROP VIEW IF EXISTS v_trace_summary;

ALTER TABLE IF EXISTS traces      RENAME TO legacy_traces;
ALTER TABLE IF EXISTS observations RENAME TO legacy_observations;
ALTER TABLE IF EXISTS scores      RENAME TO legacy_scores;

-- ============================================================================
-- STEP 2: Alter sessions — add worktree identity
-- ============================================================================

ALTER TABLE sessions ADD COLUMN IF NOT EXISTS worktree_path TEXT;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS branch        TEXT;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS kind          TEXT DEFAULT 'work';

-- ============================================================================
-- STEP 3: New plans table
-- ============================================================================

CREATE TABLE IF NOT EXISTS plans (
  plan_id           TEXT PRIMARY KEY,
  project_id        TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
  session_id        TEXT NOT NULL,
  user_intent       TEXT NOT NULL,
  dag               JSONB NOT NULL DEFAULT '[]',
  approval_status   TEXT DEFAULT 'pending',
  multimodal_refs   JSONB DEFAULT '[]',
  created_at        TIMESTAMPTZ DEFAULT now(),
  approved_at       TIMESTAMPTZ,
  FOREIGN KEY (project_id, session_id) REFERENCES sessions(project_id, session_id) ON DELETE CASCADE
);

-- ============================================================================
-- STEP 4: Alter tasks — link to plan + DAG + autonomy
-- ============================================================================

ALTER TABLE tasks ADD COLUMN IF NOT EXISTS plan_id     TEXT REFERENCES plans(plan_id);
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS node_id     TEXT;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS created_by  TEXT DEFAULT 'user';
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS depends_on  JSONB DEFAULT '[]';

-- ============================================================================
-- STEP 5: New aionui_links — bridge Conductor tasks to AionUi execution
-- ============================================================================

CREATE TABLE IF NOT EXISTS aionui_links (
  link_id                TEXT PRIMARY KEY,
  task_id                UUID NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
  aionui_team_id         TEXT,
  aionui_conversation_id TEXT,
  langfuse_trace_id      TEXT,
  status                 TEXT DEFAULT 'spawned',
  created_at             TIMESTAMPTZ DEFAULT now()
);

-- ============================================================================
-- STEP 6: Chat threads + messages (Conductor general chat, not execution)
-- ============================================================================

CREATE TABLE IF NOT EXISTS chat_threads (
  thread_id   TEXT PRIMARY KEY,
  title       TEXT,
  model       TEXT,
  project_ids JSONB DEFAULT '[]',
  created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chat_messages (
  message_id  TEXT PRIMARY KEY,
  thread_id   TEXT NOT NULL REFERENCES chat_threads(thread_id) ON DELETE CASCADE,
  role        TEXT,
  content     JSONB,
  created_at  TIMESTAMPTZ DEFAULT now()
);

-- ============================================================================
-- STEP 7: agent_memory — already exists, no changes needed
-- ============================================================================

-- agent_memory already present with full schema. Keep as-is.

-- ============================================================================
-- STEP 8: Alter skill_mutations — add experiment linkage
-- ============================================================================

ALTER TABLE skill_mutations ADD COLUMN IF NOT EXISTS experiment_id TEXT;

-- ============================================================================
-- STEP 9: New experiments table
-- ============================================================================

CREATE TABLE IF NOT EXISTS experiments (
  experiment_id    TEXT PRIMARY KEY,
  agent_config_id  TEXT NOT NULL REFERENCES agent_configs(agent_config_id),
  target           TEXT,
  baseline_ref     TEXT,
  candidate_ref    TEXT,
  dataset          TEXT,
  baseline_score   NUMERIC,
  candidate_score  NUMERIC,
  decision         TEXT,
  created_at       TIMESTAMPTZ DEFAULT now()
);
