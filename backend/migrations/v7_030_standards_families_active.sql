-- v7_030 — domain_standards: add families, active, scaffold_ref, import_ref columns
-- Required by seed_standards.sql + seed_standards_addendum.sql
-- Idempotent.

BEGIN;

ALTER TABLE domain_standards ADD COLUMN IF NOT EXISTS families jsonb;
ALTER TABLE domain_standards ADD COLUMN IF NOT EXISTS active boolean DEFAULT true;
ALTER TABLE domain_standards ADD COLUMN IF NOT EXISTS scaffold_ref text;
ALTER TABLE domain_standards ADD COLUMN IF NOT EXISTS import_ref text;

COMMIT;
