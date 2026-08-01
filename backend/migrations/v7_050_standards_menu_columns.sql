BEGIN;

-- v7_050_standards_menu_columns.sql
-- Adds formulation-menu columns to domain_standards for the multi-component goal fix.
-- These support the menu query in File 01 and the deterministic subdir in build_components().
-- Idempotent via IF NOT EXISTS.

ALTER TABLE domain_standards ADD COLUMN IF NOT EXISTS selector_blurb  TEXT    DEFAULT '';
ALTER TABLE domain_standards ADD COLUMN IF NOT EXISTS capability_tags JSONB   DEFAULT '[]';
ALTER TABLE domain_standards ADD COLUMN IF NOT EXISTS default_subdir  TEXT    DEFAULT '';

COMMIT;
