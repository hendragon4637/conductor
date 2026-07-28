-- intake-svc MVP: normalized intents from failure triggers + per-project pause
-- G1: plans.origin did NOT exist despite guide assumption — added here
-- G3: clarify_rounds added to intake_intents (not just plans)
BEGIN;

CREATE TABLE IF NOT EXISTS intake_intents (
  id              BIGSERIAL PRIMARY KEY,
  origin          TEXT NOT NULL,   -- run_failed|l4_findings|plan_failed|ratify_rejected|human_feedback
  source_ref      TEXT,            -- 'run:<id>'|'l4:<id>'|'human:<ts>' (PRESERVED across reformulations)
  project_id      TEXT NOT NULL,
  intent_text     TEXT NOT NULL,   -- fully rendered goal text sent to planner
  evidence        JSONB NOT NULL DEFAULT '[]',   -- POINTERS only: ["run:abc","plan:p1","node:n3"]
  status          TEXT NOT NULL DEFAULT 'proposed',
                  -- proposed|submitted|clarifying|awaiting_ratify|running|escalated|duplicate|superseded
  attempt         INT NOT NULL DEFAULT 1,
  plan_id         TEXT,            -- correlation key for inbound plan.* events
  last_error      TEXT,
  clarify_rounds  INT NOT NULL DEFAULT 0,   -- G3: bounded clarification counter
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- plan_id is THE correlation key for event-driven handoff
CREATE UNIQUE INDEX IF NOT EXISTS uq_intents_plan_id ON intake_intents(plan_id) WHERE plan_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_intents_project_status ON intake_intents(project_id, status);
CREATE INDEX IF NOT EXISTS idx_intents_source_ref     ON intake_intents(source_ref);
CREATE INDEX IF NOT EXISTS idx_intents_stale          ON intake_intents(status, updated_at);


-- per-project policy flags (intake is NOT the only reader — planner also reads for /stop /resume)
CREATE TABLE IF NOT EXISTS project_flags (
  project_id      TEXT PRIMARY KEY,
  intake_paused   BOOLEAN NOT NULL DEFAULT false,
  paused_reason   TEXT,
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);


-- G1: plans.origin was assumed present by guides but never migrated — adding now
ALTER TABLE plans ADD COLUMN IF NOT EXISTS origin TEXT;

-- plans back-pointers for intake correlation
ALTER TABLE plans ADD COLUMN IF NOT EXISTS source_ref TEXT;
ALTER TABLE plans ADD COLUMN IF NOT EXISTS intake_id  BIGINT;

ALTER TABLE plans ADD CONSTRAINT fk_plans_intake
  FOREIGN KEY (intake_id) REFERENCES intake_intents(id) ON DELETE SET NULL;

COMMIT;
