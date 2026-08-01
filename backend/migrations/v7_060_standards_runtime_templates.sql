-- v7_060 — File 01: service_template (ensure) + publish_manifest (new)
-- Idempotent. Mirrors guide 01.3 (component menu columns).
-- selector_blurb / capability_tags / default_subdir already exist (v7_050);
-- service_template exists (v6_190); publish_manifest does NOT — added here.

BEGIN;

-- how to run a component (no-op if already present from v6_190)
ALTER TABLE domain_standards ADD COLUMN IF NOT EXISTS service_template JSONB;

-- what a component contributes to a system (File 10 worksystem depends on this)
ALTER TABLE domain_standards ADD COLUMN IF NOT EXISTS publish_manifest JSONB;

-- allow kind='assembly' (assembly-compose-v1 in seeder; v6_150 only allowed domain/planning)
ALTER TABLE domain_standards DROP CONSTRAINT IF EXISTS domain_standards_kind_check;
ALTER TABLE domain_standards ADD CONSTRAINT domain_standards_kind_check
    CHECK (kind IN ('domain', 'planning', 'assembly'));

COMMIT;
