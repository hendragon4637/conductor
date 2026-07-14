-- v6_090: Add project_id to runs table + active-run partial unique index.
--
-- Ensures at most one non-terminal run per project (defense-in-depth
-- alongside application-layer checks in /goal and /ratify endpoints).
--
-- The partial unique index excludes terminal states (done, failed, cancelled)
-- and the planning state (multiple planning attempts may coexist before
-- ratification).  Only run states that represent an in-flight execution
-- are covered: 'created' and any future active states.

BEGIN;

-- 1. Add nullable column first
ALTER TABLE runs ADD COLUMN IF NOT EXISTS project_id TEXT REFERENCES projects(project_id) ON DELETE CASCADE;

-- 2. Backfill from plans (plans.project_id is NOT NULL)
UPDATE runs
   SET project_id = p.project_id
  FROM plans p
 WHERE runs.plan_id = p.plan_id
   AND runs.project_id IS NULL;

-- 3. Make NOT NULL now that backfill is complete
ALTER TABLE runs ALTER COLUMN project_id SET NOT NULL;

-- 4. Partial unique index: only one non-terminal, non-planning run per project
CREATE UNIQUE INDEX IF NOT EXISTS idx_runs_active_project
    ON runs (project_id)
    WHERE state NOT IN ('done', 'failed', 'cancelled', 'planning');

COMMIT;
