BEGIN;

-- L4 persona simulation columns on runs
ALTER TABLE runs ADD COLUMN IF NOT EXISTS l4_standalone REAL;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS l4_acceptance REAL;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS l4_status TEXT;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS l4_reason TEXT;

-- RUN.md assertion column
ALTER TABLE runs ADD COLUMN IF NOT EXISTS run_md_present BOOLEAN;

-- needs_usage_sim gate on plans
ALTER TABLE plans ADD COLUMN IF NOT EXISTS needs_usage_sim BOOLEAN NOT NULL DEFAULT FALSE;

COMMIT;
