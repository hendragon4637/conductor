-- Conductor v4 — Triggers table already exists via 006_triggers.sql
-- This migration adds columns needed for scheduled job dispatch.

ALTER TABLE triggers ADD COLUMN IF NOT EXISTS sandboxed BOOLEAN DEFAULT true;
ALTER TABLE triggers ADD COLUMN IF NOT EXISTS job_type TEXT DEFAULT 'enrich';

