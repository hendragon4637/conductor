-- L3 Meta-Evaluation: frozen golden-set anchor + rubric refinement proposals
-- The golden set is the FROZEN human anchor. Nothing in the system writes
-- it automatically — entries come ONLY via human action (add_golden).

CREATE TABLE IF NOT EXISTS golden_set (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    node_type   TEXT NOT NULL,
    artifact_ref TEXT NOT NULL,          -- path or inline reference to the artifact
    rubric_item TEXT NOT NULL,           -- the rubric question
    human_label BOOLEAN NOT NULL,        -- TRUE = criteria met, FALSE = not met
    expected_score NUMERIC(5,4),         -- optional continuous score 0.0–1.0
    labeled_by  TEXT NOT NULL DEFAULT 'human',
    frozen      BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS rubric_refinements (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    node_type     TEXT NOT NULL,
    rationale     TEXT NOT NULL,
    old_rubric    TEXT NOT NULL,
    new_rubric    TEXT NOT NULL,
    drift_report  JSONB NOT NULL DEFAULT '{}',
    status        TEXT NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending', 'approved', 'rejected')),
    proposed_by   TEXT NOT NULL DEFAULT 'l3_meta',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_golden_set_node_type ON golden_set(node_type);
CREATE INDEX IF NOT EXISTS idx_rubric_refinements_status ON rubric_refinements(status);
