-- v4_004_node_model.sql
-- Supersedes the three-tier kind (tool/single_agent/team).
-- Every node = a team led by the built-in orchestrator.
-- The orchestrator is implicit — not stored here.
-- Depends_on already JSONB on tasks table (File 02).

-- Add members JSONB to tasks: the specialist agent_config_ids for the node
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS members JSONB DEFAULT '[]';

-- Add node_commit_tag: Conductor stores the git tag after completion
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS node_commit_tag TEXT;

-- Add gate_mode: determines how completion is gated (v1 = watcher_only)
-- Future values: 'test_cmd', 'reviewer'
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS gate_mode TEXT DEFAULT 'watcher_only';
