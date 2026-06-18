BEGIN;

ALTER TABLE plans DROP COLUMN IF EXISTS session_id;
ALTER TABLE plans DROP COLUMN IF EXISTS status;
ALTER TABLE plans ADD COLUMN IF NOT EXISTS goal TEXT;
ALTER TABLE plans ADD COLUMN IF NOT EXISTS success JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE plans ADD COLUMN IF NOT EXISTS ratified BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE plans ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1;
ALTER TABLE plans ADD COLUMN IF NOT EXISTS user_intent TEXT;

CREATE TABLE IF NOT EXISTS runs (
  id            TEXT PRIMARY KEY,
  plan_id       TEXT NOT NULL REFERENCES plans(plan_id),
  state         TEXT NOT NULL DEFAULT 'created',
  worktree_root TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  approved_at   TIMESTAMPTZ,
  finished_at   TIMESTAMPTZ,
  note          TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_plan ON runs(plan_id);

CREATE TABLE IF NOT EXISTS node_sessions (
  id                     TEXT PRIMARY KEY,
  run_id                 TEXT NOT NULL REFERENCES runs(id),
  node_id                TEXT NOT NULL,
  members                JSONB NOT NULL DEFAULT '[]'::jsonb,
  verdict                TEXT,
  l1_pass                BOOLEAN,
  goal_review            REAL,
  gate_mode              TEXT NOT NULL DEFAULT 'l1_l2',
  commit_tag             TEXT,
  attempt                INTEGER NOT NULL DEFAULT 1,
  aionui_team_id         TEXT,
  aionui_conversation_id TEXT,
  langfuse_trace_id      TEXT,
  worktree               TEXT,
  created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at            TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_ns_run ON node_sessions(run_id);
CREATE INDEX IF NOT EXISTS idx_ns_score ON node_sessions(goal_review) WHERE goal_review IS NOT NULL;

ALTER TABLE traces ADD COLUMN IF NOT EXISTS node_session_id TEXT REFERENCES node_sessions(id);
ALTER TABLE session_signals ADD COLUMN IF NOT EXISTS node_session_id TEXT REFERENCES node_sessions(id);

CREATE TABLE IF NOT EXISTS judge_trust (
  node_type     TEXT PRIMARY KEY,
  agreement     REAL,
  mae           REAL,
  trusted       BOOLEAN NOT NULL DEFAULT FALSE,
  calibrated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS golden_set (
  id            TEXT PRIMARY KEY,
  node_type     TEXT NOT NULL,
  task          TEXT NOT NULL,
  artifact_ref  TEXT NOT NULL,
  artifact_blob TEXT,
  human_label   JSONB NOT NULL,
  labeled_by    TEXT NOT NULL,
  labeled_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  frozen        BOOLEAN NOT NULL DEFAULT TRUE,
  split         TEXT NOT NULL DEFAULT 'calibration'
);
CREATE INDEX IF NOT EXISTS idx_golden_type ON golden_set(node_type, split);

COMMIT;
