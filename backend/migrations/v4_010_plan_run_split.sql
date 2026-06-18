-- v4_010_plan_run_split.sql
-- Plan loses execution status; execution state moves to runs + node_sessions.
-- Plan = durable spec (ratified, versioned, reusable).
-- Run = one execution instance with lifecycle.
-- NodeSession = per-node execution within a run.

-- Plan changes: remove approval_status, add ratified + version
ALTER TABLE plans DROP COLUMN IF EXISTS approval_status;
ALTER TABLE plans ADD COLUMN IF NOT EXISTS ratified BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE plans ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1;

-- Runs = execution instances of a plan
CREATE TABLE IF NOT EXISTS runs (
    id            TEXT PRIMARY KEY,                -- e.g. run_<uuid8>
    plan_id       TEXT NOT NULL REFERENCES plans(plan_id),
    state         TEXT NOT NULL DEFAULT 'created', -- created|approved|running|done|failed|cancelled
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    approved_at   TIMESTAMPTZ,
    finished_at   TIMESTAMPTZ,
    worktree_root TEXT,                            -- base worktree for this run
    note          TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_plan ON runs(plan_id);

-- Node sessions = per-node execution within a run
CREATE TABLE IF NOT EXISTS node_sessions (
    id            TEXT PRIMARY KEY,                -- e.g. ns_<uuid8>
    run_id        TEXT NOT NULL REFERENCES runs(id),
    node_id       TEXT NOT NULL,                   -- references the plan's node id
    backend       TEXT NOT NULL,                   -- resolved backend for this node
    worktree      TEXT,
    verdict       TEXT,                            -- running|stalled|quota|crashed|done (watcher)
    l1_pass       BOOLEAN,
    goal_review   REAL,
    commit_tag    TEXT,
    attempt       INTEGER NOT NULL DEFAULT 1,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at   TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_ns_run ON node_sessions(run_id);
