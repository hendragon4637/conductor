# soft-clay — SOURCE.md

## Origin
- Source: A/B variant `clay` in `/opt/aipc/conductor/workspace/variants-ab-test/clay/`
- open-design upstream SHA: 276b4d8e970bc143d7ad060181a89a834e3d9caf
- DESIGN.md is the authoritative contract (copied from A/B).

## Changes from source
- `tokens.css`: REGENERATED from DESIGN.md.  The A/B bundle carried an
  upstream Inter tokens.css whose values matched neither this DESIGN.md nor
  its components.html (the File 01 drift case).  tokens.css now declares
  DESIGN.md values: Warm Cream canvas, Clay Black text, fruit swatches
  (matcha/slushie/lemon/ube/pomegranate/blueberry), Roobert + Space Mono,
  compact 4px-based spacing, radius 12/24/40.
- Added swatch-derived tokens (--swatch-*) + on-accent-muted for components.
- Contrast fix: enabled --accent-on/--accent (Matcha 600 + white = AA).
