-- Planner Harness — planning session tracking
-- Reuses node_sessions for planning attempts (role='planning').
-- Adds worktree/attempts/status to plans for bounded retry lifecycle.

BEGIN;

ALTER TABLE node_sessions ADD COLUMN IF NOT EXISTS role TEXT NOT NULL DEFAULT 'execution';
-- role: 'execution' | 'planning' | 'l4'
CREATE INDEX IF NOT EXISTS idx_ns_role ON node_sessions(role);

ALTER TABLE plans ADD COLUMN IF NOT EXISTS planning_worktree TEXT;
ALTER TABLE plans ADD COLUMN IF NOT EXISTS planning_attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE plans ADD COLUMN IF NOT EXISTS planning_status TEXT;
-- planning_status: generating | validating | gated_ok | failed

COMMIT;
