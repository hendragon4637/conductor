BEGIN;

-- ── domain_standards: vetted reference standards for domains and planning ──
CREATE TABLE IF NOT EXISTS domain_standards (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug            TEXT NOT NULL UNIQUE,
    name            TEXT NOT NULL,
    kind            TEXT NOT NULL CHECK (kind IN ('domain', 'planning')),
    conventions_md  TEXT,
    tool_manifest   JSONB DEFAULT '[]',
    artifact_spec   JSONB DEFAULT '{}',
    scaffold_tree   JSONB DEFAULT '[]',
    source_repo     TEXT,
    version         INTEGER NOT NULL DEFAULT 1,
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now()
);

-- ── capabilities: link to domain standard ────────────────────────────────
ALTER TABLE capabilities ADD COLUMN IF NOT EXISTS standard_id UUID REFERENCES domain_standards(id) ON DELETE SET NULL;

-- ── runs: track which standards apply to a run ───────────────────────────
ALTER TABLE runs ADD COLUMN IF NOT EXISTS standard_ids UUID[] DEFAULT '{}';

CREATE INDEX IF NOT EXISTS idx_runs_standard_ids ON runs USING GIN (standard_ids);

COMMIT;
