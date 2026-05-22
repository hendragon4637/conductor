-- 002_harness_promotion.sql — Promote cli → harness
-- Backward compatible: keeps `cli` columns, adds `harness` columns, syncs them.

-- ─────────────────────────────────────────────────────────────────────────
-- 1) agent_configs: add harness columns
-- ─────────────────────────────────────────────────────────────────────────

ALTER TABLE agent_configs
  ADD COLUMN IF NOT EXISTS harness TEXT,
  ADD COLUMN IF NOT EXISTS harness_capabilities JSONB DEFAULT '{}'::jsonb;

-- Backfill from existing cli column
UPDATE agent_configs SET harness = cli WHERE harness IS NULL;

-- Set NOT NULL once backfilled
ALTER TABLE agent_configs ALTER COLUMN harness SET NOT NULL;

CREATE INDEX IF NOT EXISTS idx_agent_configs_harness ON agent_configs(harness);

-- ─────────────────────────────────────────────────────────────────────────
-- 2) traces: add harness column
-- ─────────────────────────────────────────────────────────────────────────

ALTER TABLE traces ADD COLUMN IF NOT EXISTS harness TEXT;

UPDATE traces SET harness = cli WHERE harness IS NULL;

ALTER TABLE traces ALTER COLUMN harness SET NOT NULL;

CREATE INDEX IF NOT EXISTS idx_traces_harness ON traces(harness);

-- ─────────────────────────────────────────────────────────────────────────
-- 3) Sync trigger: keep cli and harness in lockstep going forward
-- ─────────────────────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION trg_sync_cli_harness() RETURNS trigger AS $$
BEGIN
  -- If only one of cli / harness is set on insert, populate the other
  IF NEW.harness IS NULL AND NEW.cli IS NOT NULL THEN
    NEW.harness := NEW.cli;
  ELSIF NEW.cli IS NULL AND NEW.harness IS NOT NULL THEN
    NEW.cli := NEW.harness;
  END IF;
  -- If both set but disagree, prefer harness (authoritative going forward)
  IF NEW.cli IS NOT NULL AND NEW.harness IS NOT NULL AND NEW.cli != NEW.harness THEN
    NEW.cli := NEW.harness;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS agent_configs_sync_harness ON agent_configs;
CREATE TRIGGER agent_configs_sync_harness BEFORE INSERT OR UPDATE
  ON agent_configs FOR EACH ROW EXECUTE FUNCTION trg_sync_cli_harness();

DROP TRIGGER IF EXISTS traces_sync_harness ON traces;
CREATE TRIGGER traces_sync_harness BEFORE INSERT OR UPDATE
  ON traces FOR EACH ROW EXECUTE FUNCTION trg_sync_cli_harness();

-- ─────────────────────────────────────────────────────────────────────────
-- 4) Update v_trace_summary view to expose harness
-- ─────────────────────────────────────────────────────────────────────────

DROP VIEW IF EXISTS v_trace_summary;
CREATE VIEW v_trace_summary AS
SELECT
  t.trace_id,
  t.task_id,
  ta.session_id,
  ta.project_id,
  t.agent_config_id,
  t.harness,
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
-- 5) Seed harness_capabilities for the existing opencode config
-- ─────────────────────────────────────────────────────────────────────────

UPDATE agent_configs SET harness_capabilities = '{
  "interactive": true,
  "headless": true,
  "session_persistence": true,
  "sub_agents": true,
  "tool_calling": "native",
  "session_id_injectable": true,
  "data_format": "sqlite",
  "data_path_template": "~/.local/share/opencode/opencode.db",
  "supports_resume": true,
  "supports_prompt_flag": true
}'::jsonb
WHERE harness = 'opencode';
