-- v7_080_worksystem.sql — File 10: Worksystem (guide 10.x)
--
-- Three additions needed by the worksystem layer:
--   1. systems.goal — system L4 scenarios are generated from the SYSTEM goal
--      (l4_runner._get_system_goal queries s.goal; the column did not exist).
--      Backfilled at ratify time (system_goal.ratify_system).
--   2. runs.l4_adjustments — the compose adjustment delta (raw git diff +
--      semantic diff of compose.yml) captured at L4 completion (guide 10.6).
--      Stored on the L4 run, never in findings — a diagnostic about manifests.
--   3. runs.partial_scope — debug-subset marker for system L4 (guide 10.7):
--      {members:[...]} runs a subset and publishes nothing.

BEGIN;

ALTER TABLE systems ADD COLUMN IF NOT EXISTS goal TEXT;

ALTER TABLE runs ADD COLUMN IF NOT EXISTS l4_adjustments JSONB;

ALTER TABLE runs ADD COLUMN IF NOT EXISTS partial_scope BOOLEAN NOT NULL DEFAULT FALSE;

COMMIT;
