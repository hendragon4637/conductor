ALTER TABLE session_signals
    ADD COLUMN IF NOT EXISTS any_error BOOLEAN DEFAULT false,
    ADD COLUMN IF NOT EXISTS error_codes JSONB DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS age_s NUMERIC,
    ADD COLUMN IF NOT EXISTS watcher_node_id TEXT,
    ADD COLUMN IF NOT EXISTS signal_snapshot JSONB DEFAULT '{}'::jsonb;
