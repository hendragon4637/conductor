# editorial-serif — SOURCE.md

## Origin
- Source: A/B variant `editorial` in `/opt/aipc/conductor/workspace/variants-ab-test/editorial/`
- open-design upstream SHA: 276b4d8e970bc143d7ad060181a89a834e3d9caf
- DESIGN.md is the authoritative contract (copied from A/B).

## Changes from source
- `tokens.css`: REGENERATED from DESIGN.md.  The A/B bundle carried an
  upstream Inter tokens.css whose values matched neither this DESIGN.md nor
  its components.html (the File 01 drift case).  Per the "DESIGN.md is
  authoritative" verdict, tokens.css now declares only DESIGN.md values:
  Gelasio (display/body) + Ubuntu Mono, §2 palette, 8pt space scale, radius
  4/8/9999, container 720px, sections 96/64/48px.
- `reference.html`: rewritten brand-neutral ("The Quarterly"), token-driven.
