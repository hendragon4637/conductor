BEGIN;

CREATE TABLE IF NOT EXISTS capability_proposals (
  name              TEXT PRIMARY KEY,
  family            TEXT NOT NULL,
  description       TEXT NOT NULL,
  quality_dimensions JSONB NOT NULL DEFAULT '[]'::jsonb,
  required_tools    JSONB NOT NULL DEFAULT '[]'::jsonb,
  source            TEXT NOT NULL DEFAULT 'example-generated',
  status            TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'approved', 'rejected')),
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  reviewed_at       TIMESTAMPTZ
);

COMMIT;
