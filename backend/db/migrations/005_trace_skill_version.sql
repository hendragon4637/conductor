-- 005_trace_skill_version.sql -- Record manifest-derived skill_id + version on each trace
ALTER TABLE traces
  ADD COLUMN IF NOT EXISTS skill_id TEXT,
  ADD COLUMN IF NOT EXISTS skill_version TEXT;

CREATE INDEX IF NOT EXISTS idx_traces_skill ON traces(skill_id, skill_version);
