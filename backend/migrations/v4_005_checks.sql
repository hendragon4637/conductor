-- Evaluator checks support
-- Adds JSONB column for per-node evaluation checks.

ALTER TABLE tasks ADD COLUMN IF NOT EXISTS checks JSONB DEFAULT '[]';
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS checks_version INTEGER DEFAULT 1;
