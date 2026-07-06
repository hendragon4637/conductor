-- Skills — add store_path, has_scripts, requires_setup columns
-- 2026-07-06
-- Run: docker exec -i postgres psql -U aipc -d aipc_conductor < backend/migrations/v6_070_skills_store.sql

BEGIN;

ALTER TABLE skills ADD COLUMN IF NOT EXISTS store_path    TEXT;       -- path to skill folder on disk
ALTER TABLE skills ADD COLUMN IF NOT EXISTS has_scripts   BOOLEAN DEFAULT FALSE;
ALTER TABLE skills ADD COLUMN IF NOT EXISTS requires_setup BOOLEAN DEFAULT FALSE;

COMMIT;
