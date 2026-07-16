BEGIN;

CREATE TABLE IF NOT EXISTS failure_events (
  id              TEXT PRIMARY KEY,
  run_id          TEXT,
  node_session_id TEXT,
  plan_id         TEXT,
  project_id      TEXT,
  capability      TEXT,
  agent_config    TEXT,
  backend         TEXT,
  goal_kind       TEXT,
  loop_tier       TEXT,                                 -- initial | steering | remediation
  failure_stage   TEXT,                                 -- planning | execution | evaluation | merge | continuation | infra
  primary_tag     TEXT NOT NULL,                        -- spec | coordination | verification | infra
  tags            JSONB NOT NULL DEFAULT '[]',
  evidence        JSONB,                                -- pointers to l2_feedback dims, error strings, steer count
  note            TEXT,
  labeled_by      TEXT NOT NULL DEFAULT 'llm',           -- llm now, human later, same table
  created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_fe_mine
  ON failure_events(capability, agent_config, primary_tag, goal_kind);

COMMIT;
