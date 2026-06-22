-- v4_051_redecomp_fields.sql
-- Adds re-decomposition lineage fields to node_sessions so future local
-- re-decomposition (a too-big node -> sub-DAG) doesn't require a migration.
-- SCHEMA-ONLY: the rewire logic is deferred; fields + guards present now.
ALTER TABLE node_sessions ADD COLUMN IF NOT EXISTS parent_node_id TEXT;
ALTER TABLE node_sessions ADD COLUMN IF NOT EXISTS depth INTEGER NOT NULL DEFAULT 0;
ALTER TABLE node_sessions ADD COLUMN IF NOT EXISTS superseded_by TEXT;
