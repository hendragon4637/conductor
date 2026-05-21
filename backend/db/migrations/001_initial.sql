-- 001_initial.sql — AIPC Conductor initial schema
-- Run with: psql -U aipc -h localhost -d aipc_conductor -f 001_initial.sql

-- ─────────────────────────────────────────────────────────────────────────
-- Extensions
-- ─────────────────────────────────────────────────────────────────────────

CREATE EXTENSION IF NOT EXISTS "pgcrypto";    -- for gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";   -- backup if pgcrypto absent

-- ─────────────────────────────────────────────────────────────────────────
-- Reference table: agent_configs (synced from YAML)
-- ─────────────────────────────────────────────────────────────────────────

CREATE TABLE agent_configs (
  agent_config_id TEXT PRIMARY KEY,
  -- format: <cli>:<domain>-<role>  e.g. "opencode:backend-executor"
  cli              TEXT NOT NULL,
  domain           TEXT NOT NULL,
  role             TEXT NOT NULL,
  pattern          TEXT NOT NULL,
  -- 'standalone' | 'PEV' | 'designer-critic' | 'reflexion' | 'searcher-synthesizer' | 'custom'

  input_spec_schema  TEXT,      -- schema name in /schemas/ dir, e.g. 'reformulated_task'
  output_spec_schema TEXT,      -- e.g. 'contribution_receipt'

  routing_rules    JSONB NOT NULL DEFAULT '{}'::jsonb,
  -- shape validated against /schemas/routing_rules.schema.json by app layer

  skill_path       TEXT,        -- absolute path, e.g. /opt/aipc/conductor/skills/backend/executor/SKILL.md
  system_prompt    TEXT,        -- injected at spawn time
  allowed_tools    TEXT[],      -- e.g. ['read','write','bash','lsp']
  permission_policy JSONB DEFAULT '{}'::jsonb,
  model_preference TEXT,        -- e.g. 'minimax/minimax-m2.5:free' (free tier)

  active           BOOLEAN NOT NULL DEFAULT TRUE,
  version          INTEGER NOT NULL DEFAULT 1,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_agent_configs_active ON agent_configs(active) WHERE active = TRUE;
CREATE INDEX idx_agent_configs_domain ON agent_configs(domain);

-- ─────────────────────────────────────────────────────────────────────────
-- projects = git repos
-- ─────────────────────────────────────────────────────────────────────────

CREATE TABLE projects (
  project_id       TEXT PRIMARY KEY
    CHECK (project_id ~ '^[a-z0-9][a-z0-9-]*[a-z0-9]$'),
  -- enforce slug: lowercase, hyphens, no leading/trailing dash
  name             TEXT NOT NULL,  -- display name; can have spaces, capitals
  description      TEXT,
  system_prompt    TEXT,           -- injected into AGENTS.md / CLAUDE.md / GEMINI.md
  repo_path        TEXT NOT NULL,  -- absolute, e.g. /opt/aipc/conductor/workspace/backend-api
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  archived         BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX idx_projects_archived ON projects(archived);

-- ─────────────────────────────────────────────────────────────────────────
-- sessions = git branches
-- ─────────────────────────────────────────────────────────────────────────

CREATE TABLE sessions (
  session_id       TEXT NOT NULL,
  -- branch name; we DO permit slashes (feat/oauth) but no .. or .lock
  project_id       TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
  user_intent      TEXT,
  status           TEXT NOT NULL DEFAULT 'active'
    CHECK (status IN ('active', 'paused', 'merged', 'abandoned')),
  base_branch      TEXT NOT NULL DEFAULT 'main',
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (project_id, session_id)
);

CREATE INDEX idx_sessions_status ON sessions(status);
CREATE INDEX idx_sessions_project ON sessions(project_id);

-- ─────────────────────────────────────────────────────────────────────────
-- tasks = flexible work units
-- ─────────────────────────────────────────────────────────────────────────

CREATE TABLE tasks (
  task_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id       TEXT NOT NULL,
  session_id       TEXT NOT NULL,
  user_intent      TEXT NOT NULL,
  reformulated_spec JSONB,
  status           TEXT NOT NULL DEFAULT 'open'
    CHECK (status IN ('open', 'in_progress', 'done', 'abandoned', 'blocked')),
  completion_signal TEXT,
  -- 'verifier_approved' | 'critic_approved' | 'manual_done' | 'max_iterations' | null
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),

  FOREIGN KEY (project_id, session_id)
    REFERENCES sessions(project_id, session_id)
    ON DELETE CASCADE
);

CREATE INDEX idx_tasks_session ON tasks(project_id, session_id);
CREATE INDEX idx_tasks_status ON tasks(status);

-- ─────────────────────────────────────────────────────────────────────────
-- traces = one CLI invocation = one "room"
-- ─────────────────────────────────────────────────────────────────────────

CREATE TABLE traces (
  trace_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  task_id          UUID NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,

  agent_config_id  TEXT NOT NULL REFERENCES agent_configs(agent_config_id),
  role             TEXT NOT NULL,                  -- denormalized from agent_config for query speed

  cli              TEXT NOT NULL,                  -- 'opencode' | 'claude_code' | 'gemini' | ...
  cli_session_id   TEXT,                           -- the CLI's own session UUID
  cli_session_path TEXT,                           -- absolute path to native session file/dir

  input_spec       JSONB,                          -- typed AgentMessage in
  output_spec      JSONB,                          -- typed AgentMessage out

  preceding_trace_id UUID REFERENCES traces(trace_id),  -- optional handoff lineage

  status           TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'spawned', 'running', 'awaiting_hitl', 'complete', 'failed', 'abandoned')),
  ended_reason     TEXT,
  -- 'completed' | 'cli_closed' | 'timeout' | 'user_abort' | 'exit_nonzero' | 'spec_invalid'

  skill_snapshot_hash TEXT,                        -- SHA256 of SKILL.md content at spawn time
  skill_path       TEXT,                           -- copy of agent_configs.skill_path at spawn

  terminates_task  BOOLEAN NOT NULL DEFAULT FALSE,
  -- did this trace end the parent task per routing rules?

  total_tokens     INTEGER,
  total_cost_usd   NUMERIC(10, 6),
  total_hitl       INTEGER DEFAULT 0,
  total_observations INTEGER DEFAULT 0,

  -- hand-labeled fields (week 1 critical)
  manual_label     TEXT
    CHECK (manual_label IS NULL OR manual_label IN ('pass', 'fail', 'partial')),
  failure_mode     TEXT,
  manual_notes     TEXT,
  labeled_at       TIMESTAMPTZ,

  started_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  ended_at         TIMESTAMPTZ,

  -- distributed tracing linkage (Langfuse)
  langfuse_trace_id TEXT
);

CREATE INDEX idx_traces_task ON traces(task_id);
CREATE INDEX idx_traces_agent_config ON traces(agent_config_id);
CREATE INDEX idx_traces_status ON traces(status);
CREATE INDEX idx_traces_cli_session ON traces(cli_session_id);
CREATE INDEX idx_traces_preceding ON traces(preceding_trace_id);
CREATE INDEX idx_traces_manual_label ON traces(manual_label) WHERE manual_label IS NOT NULL;
CREATE INDEX idx_traces_ended_at ON traces(ended_at);

-- ─────────────────────────────────────────────────────────────────────────
-- observations = steps within a trace (CLI tool calls, sub-agents)
-- ─────────────────────────────────────────────────────────────────────────

CREATE TABLE observations (
  observation_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  trace_id         UUID NOT NULL REFERENCES traces(trace_id) ON DELETE CASCADE,
  parent_observation_id UUID REFERENCES observations(observation_id) ON DELETE CASCADE,

  step_index       INTEGER,
  type             TEXT NOT NULL,
  -- 'llm_call' | 'tool_call' | 'sub_agent' | 'plan_step' | 'file_edit' | 'permission_request' | 'message'

  tool_name        TEXT,
  input            JSONB,
  output           JSONB,
  reasoning_text   TEXT,

  tokens_input     INTEGER,
  tokens_output    INTEGER,
  cost_usd         NUMERIC(10, 6),
  latency_ms       INTEGER,

  status           TEXT
    CHECK (status IS NULL OR status IN ('ok', 'error', 'hitl_blocked', 'running')),
  error            TEXT,

  started_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  ended_at         TIMESTAMPTZ,

  -- source fingerprint for idempotent re-ingest
  source_fingerprint TEXT UNIQUE
  -- e.g. "opencode:<session_id>:<message_id>:<part_id>"
);

CREATE INDEX idx_observations_trace ON observations(trace_id);
CREATE INDEX idx_observations_parent ON observations(parent_observation_id);
CREATE INDEX idx_observations_type ON observations(type);
CREATE INDEX idx_observations_step ON observations(trace_id, step_index);

-- ─────────────────────────────────────────────────────────────────────────
-- hitl_events = HITL approval/edit/reject events
-- ─────────────────────────────────────────────────────────────────────────

CREATE TABLE hitl_events (
  hitl_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  observation_id   UUID NOT NULL REFERENCES observations(observation_id) ON DELETE CASCADE,
  trace_id         UUID NOT NULL REFERENCES traces(trace_id) ON DELETE CASCADE,

  prompt           TEXT NOT NULL,
  decision         TEXT
    CHECK (decision IN ('approve_once', 'approve_always', 'reject', 'edit', 'timeout', 'pending')),
  edit_payload     JSONB,
  decided_by       TEXT,            -- 'user' | 'auto_policy' | 'timeout'

  asked_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  answered_at      TIMESTAMPTZ
);

CREATE INDEX idx_hitl_trace ON hitl_events(trace_id);
CREATE INDEX idx_hitl_observation ON hitl_events(observation_id);
CREATE INDEX idx_hitl_decision ON hitl_events(decision);

-- ─────────────────────────────────────────────────────────────────────────
-- scores = eval scores (multi-track, multi-dimension)
-- ─────────────────────────────────────────────────────────────────────────

CREATE TABLE scores (
  score_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  trace_id         UUID NOT NULL REFERENCES traces(trace_id) ON DELETE CASCADE,
  observation_id   UUID REFERENCES observations(observation_id) ON DELETE CASCADE,
  -- if NULL: trace-level score; otherwise: scoped to one observation

  track            TEXT NOT NULL
    CHECK (track IN ('deterministic', 'judge', 'redteam', 'manual')),
  dimension        TEXT NOT NULL
    CHECK (dimension IN ('correctness', 'efficiency', 'safety', 'reasoning', 'composite', 'completeness')),

  value            NUMERIC(5, 4) NOT NULL,
  -- normalize to 0.0-1.0 always; UI multiplies for display

  clauses_violated TEXT[],
  judge_metadata   JSONB,
  rubric_version   TEXT,

  created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_scores_trace ON scores(trace_id);
CREATE INDEX idx_scores_track_dim ON scores(track, dimension);

-- ─────────────────────────────────────────────────────────────────────────
-- skill_mutations = ratchet trail
-- ─────────────────────────────────────────────────────────────────────────

CREATE TABLE skill_mutations (
  mutation_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  agent_config_id  TEXT NOT NULL REFERENCES agent_configs(agent_config_id),
  skill_path       TEXT NOT NULL,

  trigger_trace_ids UUID[],          -- which trace failures triggered this
  pre_score        NUMERIC(5, 4),
  post_score       NUMERIC(5, 4),

  pre_hash         TEXT,              -- SHA256 of SKILL.md before
  post_hash        TEXT,              -- SHA256 after
  diff             TEXT,              -- unified diff
  rationale        TEXT,

  proposed_by      TEXT,              -- 'hermes_cko' | 'human' | 'autoresearch'
  kept             BOOLEAN,           -- ratchet decision; NULL while in-flight
  decision_at      TIMESTAMPTZ,

  created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_mutations_config ON skill_mutations(agent_config_id);
CREATE INDEX idx_mutations_kept ON skill_mutations(kept);

-- ─────────────────────────────────────────────────────────────────────────
-- Helper: updated_at triggers
-- ─────────────────────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION trg_set_updated_at() RETURNS trigger AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER projects_updated_at BEFORE UPDATE ON projects
  FOR EACH ROW EXECUTE FUNCTION trg_set_updated_at();
CREATE TRIGGER sessions_updated_at BEFORE UPDATE ON sessions
  FOR EACH ROW EXECUTE FUNCTION trg_set_updated_at();
CREATE TRIGGER tasks_updated_at BEFORE UPDATE ON tasks
  FOR EACH ROW EXECUTE FUNCTION trg_set_updated_at();
CREATE TRIGGER agent_configs_updated_at BEFORE UPDATE ON agent_configs
  FOR EACH ROW EXECUTE FUNCTION trg_set_updated_at();

-- ─────────────────────────────────────────────────────────────────────────
-- View: trace_summary (denormalized for UI)
-- ─────────────────────────────────────────────────────────────────────────

CREATE VIEW v_trace_summary AS
SELECT
  t.trace_id,
  t.task_id,
  ta.session_id,
  ta.project_id,
  t.agent_config_id,
  t.role,
  t.status,
  t.manual_label,
  t.failure_mode,
  t.cli_session_id,
  t.total_tokens,
  t.total_cost_usd,
  t.total_hitl,
  t.total_observations,
  t.started_at,
  t.ended_at,
  EXTRACT(EPOCH FROM (COALESCE(t.ended_at, now()) - t.started_at)) AS duration_s
FROM traces t
JOIN tasks ta ON ta.task_id = t.task_id;

-- ─────────────────────────────────────────────────────────────────────────
-- Done
-- ─────────────────────────────────────────────────────────────────────────
