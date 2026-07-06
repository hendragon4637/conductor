-- Profiles — Schema migration (neutral fields + skills + capability<->skill map)
-- 2026-07-06
-- Run: docker exec -i postgres psql -U aipc -d aipc_conductor < backend/migrations/v6_050_profiles.sql

BEGIN;

-- ── agent_configs: neutral fields for imported profiles ─────────────────────
ALTER TABLE agent_configs ADD COLUMN IF NOT EXISTS import_ref    TEXT;        -- repo + path provenance
ALTER TABLE agent_configs ADD COLUMN IF NOT EXISTS raw_definition JSONB;      -- original parsed frontmatter (harness-neutral)
ALTER TABLE agent_configs ADD COLUMN IF NOT EXISTS backend_targets JSONB DEFAULT '["opencode"]';  -- which harnesses can run this

UPDATE agent_configs SET source = 'hand' WHERE source = 'example-generated';

-- ── skills table (harness-neutral) ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS skills (
  skill_id      TEXT PRIMARY KEY,          -- slug
  name          TEXT NOT NULL,
  description   TEXT,
  body          TEXT NOT NULL,             -- the skill markdown (neutral)
  tools         JSONB DEFAULT '[]',
  source        TEXT DEFAULT 'imported',
  import_ref    TEXT,
  updated_at    TIMESTAMPTZ DEFAULT now()
);

-- ── capability <-> skill map (drives per-worktree selection) ────────────────
CREATE TABLE IF NOT EXISTS capability_skills (
  capability TEXT NOT NULL REFERENCES capabilities(name),
  skill_id   TEXT NOT NULL REFERENCES skills(skill_id),
  PRIMARY KEY (capability, skill_id)
);

-- ── agent_config <-> skill (optional direct link) ──────────────────────────
CREATE TABLE IF NOT EXISTS agent_config_skills (
  agent_config_id TEXT NOT NULL REFERENCES agent_configs(agent_config_id),
  skill_id        TEXT NOT NULL REFERENCES skills(skill_id),
  PRIMARY KEY (agent_config_id, skill_id)
);

COMMIT;
