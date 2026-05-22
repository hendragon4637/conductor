-- 006_triggers.sql -- Declarative triggers for auto-spawning tasks.

CREATE TABLE IF NOT EXISTS triggers (
  trigger_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name              TEXT NOT NULL,
  description       TEXT,

  trigger_type      TEXT NOT NULL
    CHECK (trigger_type IN ('cron', 'webhook', 'api', 'event')),

  -- Spawn target
  project_id        TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
  session_id        TEXT NOT NULL,
  agent_config_id   TEXT NOT NULL REFERENCES agent_configs(agent_config_id),

  -- For cron triggers
  cron_expression   TEXT,
  -- e.g. "0 3 * * *"  (3am daily) -- standard 5-field
  last_fired_at     TIMESTAMPTZ,
  next_fire_at      TIMESTAMPTZ,

  -- For webhook triggers (week 5+)
  webhook_secret    TEXT,

  -- For event triggers (week 6+)
  event_filter      JSONB,
  -- e.g. {"on": "trace.failed", "agent_config": "opencode:backend-executor"}

  -- Common
  intent_template   TEXT NOT NULL,
  -- The user_intent for the spawned task. May include {{vars}} substituted at fire time.

  input_spec_override JSONB,
  -- Optional fully specified input_spec; bypasses default reformulation

  active            BOOLEAN NOT NULL DEFAULT TRUE,
  fire_count        INTEGER NOT NULL DEFAULT 0,

  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),

  CONSTRAINT trigger_type_specific CHECK (
    (trigger_type = 'cron'    AND cron_expression IS NOT NULL) OR
    (trigger_type = 'webhook' AND webhook_secret  IS NOT NULL) OR
    (trigger_type = 'api') OR
    (trigger_type = 'event'   AND event_filter    IS NOT NULL)
  ),

  FOREIGN KEY (project_id, session_id)
    REFERENCES sessions(project_id, session_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_triggers_active ON triggers(active) WHERE active;
CREATE INDEX IF NOT EXISTS idx_triggers_type ON triggers(trigger_type);
CREATE INDEX IF NOT EXISTS idx_triggers_next_fire ON triggers(next_fire_at) WHERE active AND trigger_type = 'cron';

DROP TRIGGER IF EXISTS triggers_updated_at ON triggers;
CREATE TRIGGER triggers_updated_at BEFORE UPDATE ON triggers
  FOR EACH ROW EXECUTE FUNCTION trg_set_updated_at();

-- Add triggered_by to tasks so we can attribute auto-spawned ones
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS triggered_by UUID REFERENCES triggers(trigger_id);

CREATE INDEX IF NOT EXISTS idx_tasks_triggered ON tasks(triggered_by) WHERE triggered_by IS NOT NULL;
