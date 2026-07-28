BEGIN;

-- L4 MVP v2: reuse runs table for L4 sessions
-- kind distinguishes execution runs from L4 harness runs
ALTER TABLE runs ADD COLUMN IF NOT EXISTS kind          TEXT NOT NULL DEFAULT 'execution';
ALTER TABLE runs ADD COLUMN IF NOT EXISTS parent_run_id TEXT;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS l4_scenarios  JSONB;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS l4_report     JSONB;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS l4_structural TEXT;
    -- ok | missing_file | parse_error | schema_error | path_error | inconsistent
ALTER TABLE runs ADD COLUMN IF NOT EXISTS spec_hash     TEXT;
    -- hash(goal+spec) — enables future scenario reuse and graduation

CREATE INDEX IF NOT EXISTS idx_runs_l4_parent ON runs(parent_run_id) WHERE kind = 'l4';
CREATE INDEX IF NOT EXISTS idx_runs_kind      ON runs(project_id, kind, created_at DESC);

-- Lock exemption: L4 runs must not count against the one-active-run-per-project constraint.
-- Drop the old unique index and recreate with an additional clause excluding kind='l4'.
DROP INDEX IF EXISTS idx_runs_active_project;
CREATE UNIQUE INDEX idx_runs_active_project
    ON runs(project_id)
    WHERE state <> ALL (ARRAY['done'::text, 'failed'::text, 'cancelled'::text, 'planning'::text])
      AND (kind IS NULL OR kind != 'l4');

COMMIT;
