-- v7_100_design_layout_v2.sql — seed design-layout-v2 standard + variants library
--
-- Clones the design-layout standard into a new design-layout-v2 row whose
-- scaffold_ref points at the variant-aware scaffold (scaffolds_store/
-- design-layout-v2) and populates the `variants` JSONB added in v7_090.
--
-- The existing design-layout (v1) row is DEACTIVATED (active=false) so the
-- standard menu — which filters ``active`` — only surfaces the variant-
-- bearing design-layout-v2 for selection.  v1 stays in the table for
-- already-in-flight projects that reference it.
--
-- VARIANT BLURBS MUST STATE WHAT THE VARIANT IS NOT (02.5) — the limitation
-- is what makes selection correct rather than plausible.
BEGIN;

INSERT INTO domain_standards (
  slug, name, kind, conventions_md, tool_manifest, artifact_spec,
  scaffold_tree, source_repo, version, families, active, scaffold_ref,
  import_ref, selector_blurb, capability_tags, default_subdir, variants
)
SELECT
  'design-layout-v2',
  'Design Layout (open-design, variants)',
  'domain',
  conventions_md,
  tool_manifest,
  artifact_spec,
  scaffold_tree,
  source_repo,
  version + 1,
  families,
  true,
  '/opt/aipc/conductor/scaffolds_store/design-layout-v2',
  import_ref,
  'Visual design artifacts from one of 5 curated variant systems (technical-dense, editorial-serif, soft-clay, brutalism, mono). DESIGN.md tokens, WCAG AA contrast, exports/ directory.',
  capability_tags,
  'design',
  '{
    "technical-dense": {
      "dir":    "variants/technical-dense",
      "blurb":  "dense data-rich dashboard: compact 8px rhythm, tight tables, restrained hashcorp-red accent. NOT for long-form editorial or playful consumer pages.",
      "source": "open-design/design-systems/hashicorp@276b4d8e970bc143d7ad060181a89a834e3d9caf"
    },
    "editorial-serif": {
      "dir":    "variants/editorial-serif",
      "blurb":  "long-form editorial: serif display, generous measure, restrained palette. NOT for dense dashboards or high-interaction utilities.",
      "source": "open-design/design-systems/editorial@276b4d8e970bc143d7ad060181a89a834e3d9caf"
    },
    "soft-clay": {
      "dir":    "variants/soft-clay",
      "blurb":  "friendly rounded SaaS: warm cream canvas, fruit-swatch palette, playful hover. NOT for data-dense or formal/legal artifacts.",
      "source": "open-design/design-systems/clay@276b4d8e970bc143d7ad060181a89a834e3d9caf"
    },
    "brutalism": {
      "dir":    "variants/brutalism",
      "blurb":  "bold concrete anti-design: hard black lines, terracotta accent, loud typography. NOT for polished corporate or subtle minimal deliverables.",
      "source": "open-design/design-systems/brutalism@276b4d8e970bc143d7ad060181a89a834e3d9caf"
    },
    "mono": {
      "dir":    "variants/mono",
      "blurb":  "matrix hacker-chic monospace: high-contrast, compact, terminal aesthetic. NOT for luxury branding or warm editorial reads.",
      "source": "open-design/design-systems/mono@276b4d8e970bc143d7ad060181a89a834e3d9caf"
    }
  }'::jsonb
FROM domain_standards
WHERE slug = 'design-layout'
  AND NOT EXISTS (SELECT 1 FROM domain_standards WHERE slug = 'design-layout-v2');

UPDATE domain_standards SET active = false WHERE slug = 'design-layout';

COMMIT;