-- 007_hooks.sql — Trace lifecycle hooks (schema-only in week 3).

CREATE TABLE IF NOT EXISTS hooks (
  hook_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name              TEXT NOT NULL,
  description       TEXT,

  event             TEXT NOT NULL
    CHECK (event IN (
      'trace.pre_spawn',     -- before trace row inserted
      'trace.spawned',       -- right after spawn
      'trace.completed',     -- on successful completion
      'trace.failed',        -- on failure
      'trace.abandoned',     -- watchdog marked abandoned
      'trace.labeled',       -- hand-label saved
      'trace.scored'         -- eval scored
    )),

  -- Filter: hook fires only if trace matches these conditions
  filter            JSONB NOT NULL DEFAULT '{}'::jsonb,

  -- Action
  action            TEXT NOT NULL,
  -- format: "internal:<name>" | "webhook:<url>" | "shell:<path>"

  action_payload    JSONB,

  -- Run order if multiple hooks fire for the same event (lower = earlier)
  priority          INTEGER NOT NULL DEFAULT 100,

  -- State
  active            BOOLEAN NOT NULL DEFAULT TRUE,
  last_fired_at     TIMESTAMPTZ,
  fire_count        INTEGER NOT NULL DEFAULT 0,
  error_count       INTEGER NOT NULL DEFAULT 0,
  last_error        TEXT,

  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_hooks_event ON hooks(event) WHERE active;
CREATE INDEX IF NOT EXISTS idx_hooks_priority ON hooks(event, priority) WHERE active;

DROP TRIGGER IF EXISTS hooks_updated_at ON hooks;
CREATE TRIGGER hooks_updated_at BEFORE UPDATE ON hooks
  FOR EACH ROW EXECUTE FUNCTION trg_set_updated_at();

-- Log of every hook dispatch attempt (for debugging / audit)
CREATE TABLE IF NOT EXISTS hook_invocations (
  invocation_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  hook_id           UUID NOT NULL REFERENCES hooks(hook_id) ON DELETE CASCADE,
  trace_id          UUID REFERENCES traces(trace_id) ON DELETE SET NULL,
  event             TEXT NOT NULL,

  dispatched_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  status            TEXT
    CHECK (status IN ('logged', 'success', 'failed', 'skipped')),
  result_summary    TEXT,
  duration_ms       INTEGER
);

CREATE INDEX IF NOT EXISTS idx_invocations_hook ON hook_invocations(hook_id);
CREATE INDEX IF NOT EXISTS idx_invocations_trace ON hook_invocations(trace_id);
CREATE INDEX IF NOT EXISTS idx_invocations_dispatched ON hook_invocations(dispatched_at);
