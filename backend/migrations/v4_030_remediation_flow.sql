BEGIN;

-- Add remediation flow columns to node_sessions
ALTER TABLE node_sessions ADD COLUMN IF NOT EXISTS remediation_of TEXT REFERENCES node_sessions(id);
ALTER TABLE node_sessions ADD COLUMN IF NOT EXISTS feedback JSONB;
ALTER TABLE node_sessions ADD COLUMN IF NOT EXISTS fail_reason TEXT;

-- Index for finding prior attempts of a node within a run
CREATE INDEX IF NOT EXISTS idx_ns_node_attempt ON node_sessions(run_id, node_id, attempt);

COMMIT;
