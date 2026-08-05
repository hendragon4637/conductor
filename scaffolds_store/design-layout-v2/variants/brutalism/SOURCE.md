# brutalism — SOURCE.md

## Origin
- Source: open-design system `brutalism` in `/opt/aipc/open-design/design-systems/brutalism/`
- Upstream SHA: 276b4d8e970bc143d7ad060181a89a834e3d9caf

## Changes from source
- `DESIGN.md`: copied verbatim (Darker Grotesque display, §2 primary
  #DD614C, §4 spacing 4/8/12/16/24/32, §7 motion 150–250ms).
- `tokens.css`: REGENERATED — the upstream open-design tokens.css declared a
  loud-yellow palette (#ffef5a accent, #f5f1e8 bg) contradicting its own
  DESIGN.md §2 (Primary #DD614C terracotta).  Now DESIGN.md-faithful.
- Contrast: --accent-on is dark (#111827) because white fails AA on terracotta.
- `reference.html`: new brand-neutral one-pager ("Forgeworks Steel"),
  token-driven, hard-border brutalist styling.
