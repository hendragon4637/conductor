-- v7_070_run_outcomes.sql — Run completion outcomes (File 06, guide 06.1)
--
-- Three INDEPENDENT outcome families recorded on the run row:
--   merge  : merged | blocked | skipped   (independent of run outcome)
--   image  : built  | failed  | skipped   (independent of both)
--   publish: published | stale | skipped  (File 10)
--
-- DEFAULT 'merged' keeps existing rows semantically correct — they did merge.
-- The partial indexes ARE the escalation queues (no extra tables).

BEGIN;

-- merge outcome
ALTER TABLE runs ADD COLUMN IF NOT EXISTS merge_status TEXT NOT NULL DEFAULT 'merged';  -- merged|blocked|skipped
ALTER TABLE runs ADD COLUMN IF NOT EXISTS merge_ref    TEXT;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS merge_error  TEXT;

-- image outcome
ALTER TABLE runs ADD COLUMN IF NOT EXISTS image_status TEXT NOT NULL DEFAULT 'skipped'; -- built|failed|skipped
ALTER TABLE runs ADD COLUMN IF NOT EXISTS image_tag    TEXT;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS image_error  TEXT;

-- publish outcome (File 10)
ALTER TABLE runs ADD COLUMN IF NOT EXISTS publish_status TEXT NOT NULL DEFAULT 'skipped'; -- published|stale|skipped
ALTER TABLE runs ADD COLUMN IF NOT EXISTS publish_error  TEXT;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS publish_commit TEXT;

CREATE INDEX IF NOT EXISTS idx_runs_merge_blocked ON runs(project_id, merge_status) WHERE merge_status='blocked';
CREATE INDEX IF NOT EXISTS idx_runs_image_failed  ON runs(project_id, image_status) WHERE image_status='failed';

COMMIT;
