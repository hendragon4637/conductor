-- v7_110_prompt_version.sql
-- Add prompt_version to plans for the formulator ratchet service.
-- The ratchet needs a short explicit version tag (v1/v2/...) to compare
-- plans across prompt mutations; before this, the rendered prompt was
-- never persisted so no version could be recovered. Backfill existing
-- rows to 'v1' (the prompt text they were formulated under).
ALTER TABLE plans ADD COLUMN IF NOT EXISTS prompt_version TEXT NOT NULL DEFAULT 'v1';

-- Backfill: everything existing was formulated under the current
-- FORMULATE_PROMPT (the v1 baseline for the ratchet).
UPDATE plans SET prompt_version = 'v1' WHERE prompt_version IS NULL;
