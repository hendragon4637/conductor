-- System Layer: systems, project membership, dependencies, pending goals,
-- system proposals, dep_shas, service descriptors, and new-project proposals.
--
-- Every added column is nullable or defaulted — no existing row is invalidated.
-- Backfill runs afterwards, then constraints are added.
BEGIN;

-- ── systems: a label + a grouping, no code, no architecture ─────────────────
CREATE TABLE IF NOT EXISTS systems (
  system_id    TEXT PRIMARY KEY,
  name         TEXT NOT NULL,
  description  TEXT,
  glossary     JSONB NOT NULL DEFAULT '{}',
  persona_id   TEXT NOT NULL DEFAULT 'default',
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── projects gain system membership, kind, persona_id, status ───────────────
ALTER TABLE projects ADD COLUMN IF NOT EXISTS system_id  TEXT;
ALTER TABLE projects ADD COLUMN IF NOT EXISTS kind       TEXT NOT NULL DEFAULT 'component';
  -- component | assembly
ALTER TABLE projects ADD COLUMN IF NOT EXISTS persona_id TEXT NOT NULL DEFAULT 'default';
ALTER TABLE projects ADD COLUMN IF NOT EXISTS status     TEXT NOT NULL DEFAULT 'active';
  -- active | archived

-- ── dependency edges (junction, not an array: needs per-edge metadata) ──────
CREATE TABLE IF NOT EXISTS project_dependencies (
  project_id            TEXT NOT NULL,
  depends_on_project_id TEXT NOT NULL,
  dep_name              TEXT NOT NULL,   -- directory name under deps/ (default = dependency's name)
  consumed_by           TEXT,            -- component subdir, NULL = whole project
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (project_id, depends_on_project_id),
  CHECK (project_id <> depends_on_project_id)
);
CREATE INDEX IF NOT EXISTS idx_deps_reverse ON project_dependencies(depends_on_project_id);

-- ── deferred goal queue: goal SPECS, not plans ─────────────────────────────
CREATE TABLE IF NOT EXISTS pending_goals (
  id           BIGSERIAL PRIMARY KEY,
  project_id   TEXT NOT NULL,
  raw_input    TEXT NOT NULL,
  origin       TEXT NOT NULL DEFAULT 'system_goal',
  wait_for     JSONB NOT NULL DEFAULT '[]',      -- project_ids needing a merged master
  status       TEXT NOT NULL DEFAULT 'pending',  -- pending|submitted|done|escalated|cancelled
  plan_id      TEXT,                             -- set AFTER submission
  last_error   TEXT,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_pending_status ON pending_goals(status, project_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_pending_plan ON pending_goals(plan_id) WHERE plan_id IS NOT NULL;

-- ── system proposals: write-once audit of decomposition + human edits ──────
CREATE TABLE IF NOT EXISTS system_proposals (
  id            BIGSERIAL PRIMARY KEY,
  raw_input     TEXT NOT NULL,
  proposal      JSONB NOT NULL,        -- the decompose call's validated output
  edited        JSONB,                 -- what the human changed before ratifying
  status        TEXT NOT NULL DEFAULT 'proposed',  -- proposed|ratified|rejected
  system_id     TEXT,                  -- set on ratification
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── what each run actually consumed (reproducibility-in-hindsight) ──────────
ALTER TABLE runs ADD COLUMN IF NOT EXISTS dep_shas JSONB NOT NULL DEFAULT '{}';

-- ── per-standard service descriptor template (for assembly compose) ────────
ALTER TABLE domain_standards ADD COLUMN IF NOT EXISTS service_template JSONB;

-- ── intake: proposing a NEW project (from L4 findings) ─────────────────────
ALTER TABLE intake_intents ADD COLUMN IF NOT EXISTS proposed_project JSONB;
  -- {name, domain, depends_on: [...], first_goal}

COMMIT;
