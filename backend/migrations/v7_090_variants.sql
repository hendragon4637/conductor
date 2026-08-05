-- v7_090_variants.sql — add per-standard design variant library (guide variants 02.1)
--
-- Adds one JSONB column `variants` to domain_standards.  It rides inside the
-- existing row the loader already reads (no new table, no JOIN).  Absent (={})
-- on strong-oracle standards; populated only for design-layout-v2.
--
-- Shape:
-- {
--   "<variant>": {
--     "dir":    "variants/<variant>",      # relative to scaffold_ref
--     "blurb":  "what it is / what it is NOT",
--     "source": "open-design/design-systems/<name>@<sha>"   # provenance (02.3)
--   }, ...
-- }
BEGIN;

ALTER TABLE domain_standards
  ADD COLUMN IF NOT EXISTS variants JSONB NOT NULL DEFAULT '{}'::jsonb;

COMMIT;