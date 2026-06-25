BEGIN;

-- File 08 — Worktree lifecycle tracking for runs.
-- Adds columns to manage merge-on-success / quarantine-on-failure.

ALTER TABLE runs ADD COLUMN IF NOT EXISTS worktree_status TEXT NOT NULL DEFAULT 'active';
  -- active | merged | quarantined | cleaned

ALTER TABLE runs ADD COLUMN IF NOT EXISTS merge_commit TEXT;
  -- On success: the SHA of the merge commit (run branch → main)

ALTER TABLE runs ADD COLUMN IF NOT EXISTS quarantine_tag TEXT;
  -- On failure: git tag reference (e.g. "failed/<plan_id>/<run_id>")

ALTER TABLE runs ADD COLUMN IF NOT EXISTS worktree_expires_at TIMESTAMPTZ;
  -- TTL for cleanup: 1 day for success, 7 days for failure

COMMIT;
