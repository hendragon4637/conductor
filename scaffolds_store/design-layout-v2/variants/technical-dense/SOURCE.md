# technical-dense — SOURCE.md

## Origin
- Source: A/B variant `hashicorp` in `/opt/aipc/conductor/workspace/variants-ab-test/hashicorp/`
- Manifests: `source/style-foundations.manifest.json` (+ components, tokens)
- open-design upstream SHA: 276b4d8e970bc143d7ad060181a89a834e3d9caf
- DESIGN.md retains the full style-foundations spec (palette/type/spacing/voice).

## Changes from source
- `reference.html`: rewritten brand-neutral ("Northstar Systems"), token-driven
  — every color/space via var(--token).  Chip padding corrected from inline
  refs to var(--space-1) var(--space-2).
- `tokens.css`: reused from A/B verbatim (self-consistent, declared-value 1.0).
- No contrast changes — all pairs meet WCAG AA as-shipped.
