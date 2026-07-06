BEGIN;

CREATE TABLE IF NOT EXISTS stress_goals (
    id                   TEXT PRIMARY KEY,
    domain               TEXT NOT NULL,
    scope                TEXT NOT NULL,
    title                TEXT NOT NULL,
    spec                 TEXT NOT NULL,
    expected_capabilities JSONB NOT NULL DEFAULT '[]'::jsonb,
    source               TEXT NOT NULL DEFAULT 'generated',
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMIT;
