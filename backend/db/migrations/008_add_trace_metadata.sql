-- Week 4: Add metadata JSONB column to traces for spawn_mode and future extensions.
-- This avoids polluting input_spec (which has strict schema validation).

ALTER TABLE traces ADD COLUMN IF NOT EXISTS metadata jsonb;

-- Also update the input_spec migration for the schema
