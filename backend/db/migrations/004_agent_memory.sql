-- 004_agent_memory.sql — Portable cross-harness agent memory.
-- Body content lives on disk; this table is the searchable index.

CREATE TABLE IF NOT EXISTS agent_memory (
  memory_id        TEXT PRIMARY KEY,
  scope            TEXT NOT NULL
    CHECK (scope IN ('global', 'project', 'agent_config', 'session')),

  -- scope-specific keys (nullable depending on scope)
  project_id       TEXT REFERENCES projects(project_id) ON DELETE CASCADE,
  agent_config_id  TEXT REFERENCES agent_configs(agent_config_id) ON DELETE CASCADE,
  session_id       TEXT,
  -- if scope='session' both project_id and session_id are set

  title            TEXT NOT NULL,
  tags             TEXT[] DEFAULT '{}',
  source           TEXT,
  -- 'manual' | 'ratchet' | 'imported' | 'inferred'

  file_path        TEXT NOT NULL,
  -- absolute path under /opt/aipc/conductor/memory/

  content_hash     TEXT,
  -- SHA256 of file content; bump on edit

  body_preview     TEXT,
  -- first ~280 chars for UI listing; full body lives in file

  active           BOOLEAN NOT NULL DEFAULT TRUE,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),

  -- Validity constraints per scope
  CONSTRAINT memory_scope_keys CHECK (
    (scope = 'global'       AND project_id IS NULL AND agent_config_id IS NULL AND session_id IS NULL) OR
    (scope = 'project'      AND project_id IS NOT NULL AND agent_config_id IS NULL AND session_id IS NULL) OR
    (scope = 'agent_config' AND agent_config_id IS NOT NULL) OR
    (scope = 'session'      AND project_id IS NOT NULL AND session_id IS NOT NULL)
  )
);

CREATE INDEX IF NOT EXISTS idx_memory_scope ON agent_memory(scope);
CREATE INDEX IF NOT EXISTS idx_memory_project ON agent_memory(project_id);
CREATE INDEX IF NOT EXISTS idx_memory_config ON agent_memory(agent_config_id);
CREATE INDEX IF NOT EXISTS idx_memory_session ON agent_memory(project_id, session_id);
CREATE INDEX IF NOT EXISTS idx_memory_tags ON agent_memory USING GIN (tags);
CREATE INDEX IF NOT EXISTS idx_memory_active ON agent_memory(active) WHERE active;

DROP TRIGGER IF EXISTS agent_memory_updated_at ON agent_memory;
CREATE TRIGGER agent_memory_updated_at BEFORE UPDATE ON agent_memory
  FOR EACH ROW EXECUTE FUNCTION trg_set_updated_at();
