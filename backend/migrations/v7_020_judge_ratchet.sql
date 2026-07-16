BEGIN;

-- ── judge_rubrics: versioned rubric artifacts ──────────────────────────
CREATE TABLE IF NOT EXISTS judge_rubrics (
    id             TEXT PRIMARY KEY,               -- <capability>-v<version>
    capability     TEXT NOT NULL,
    version        INTEGER NOT NULL,
    dims           JSONB NOT NULL,                 -- {anchors[], feedback_contract, bundles{}, dimensions[{id, rubric_item, weight, evaluation_steps[], calibrated, preset, golden_items}, ...], observed_dimensions[]}
    source         TEXT NOT NULL DEFAULT 'hand',   -- hand | judge_ratchet
    parent_version TEXT REFERENCES judge_rubrics(id),
    active         BOOLEAN NOT NULL DEFAULT false,
    created_at     TIMESTAMPTZ DEFAULT now(),
    UNIQUE(capability, version)
);

-- ── judge_experiments: track judge-ratchet cycles ──────────────────────
CREATE TABLE IF NOT EXISTS judge_experiments (
    id                TEXT PRIMARY KEY,
    capability        TEXT NOT NULL,
    control_rubric    TEXT REFERENCES judge_rubrics(id),
    candidate_rubric  TEXT REFERENCES judge_rubrics(id),
    mined_disagreement JSONB,
    mutation_diff     TEXT,
    rationale         TEXT,
    calib_control     JSONB,
    calib_candidate   JSONB,
    heldout_control   JSONB,
    heldout_candidate JSONB,
    decision          TEXT CHECK (decision IN ('kept', 'reverted', 'rejected_boundary')),
    judge_model       TEXT NOT NULL,
    decided_at        TIMESTAMPTZ,
    created_at        TIMESTAMPTZ DEFAULT now()
);

-- ── judge_trust: bind trust to rubric version ──────────────────────────
ALTER TABLE judge_trust ADD COLUMN IF NOT EXISTS rubric_id TEXT;

-- ── experiments: stamp main-ratchet experiments with judge identity ────
ALTER TABLE experiments ADD COLUMN IF NOT EXISTS judge_model TEXT;
ALTER TABLE experiments ADD COLUMN IF NOT EXISTS rubric_id TEXT;

COMMIT;
