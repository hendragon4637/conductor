BEGIN;

CREATE TABLE IF NOT EXISTS domain_profiles (
    domain         TEXT PRIMARY KEY,
    acceptance     JSONB NOT NULL,
    conventions    JSONB NOT NULL DEFAULT '[]'::jsonb,
    custom         JSONB NOT NULL DEFAULT '{}'::jsonb,
    version        INTEGER NOT NULL DEFAULT 1,
    source         TEXT NOT NULL DEFAULT 'example-generated',
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMIT;
