BEGIN;

ALTER TABLE skill_mutations ADD COLUMN IF NOT EXISTS target TEXT;
ALTER TABLE skill_mutations ADD COLUMN IF NOT EXISTS mined_pattern JSONB;
ALTER TABLE skill_mutations ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'proposed';

-- Migrate legacy kept boolean → status
UPDATE skill_mutations SET status = 'kept' WHERE kept = true AND status = 'proposed';
UPDATE skill_mutations SET status = 'reverted' WHERE kept = false AND status = 'proposed';

COMMIT;
