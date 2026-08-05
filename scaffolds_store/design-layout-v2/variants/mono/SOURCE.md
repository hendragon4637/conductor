# mono — SOURCE.md

## Origin
- Source: open-design system `mono` in `/opt/aipc/open-design/design-systems/mono/`
- Upstream SHA: 276b4d8e970bc143d7ad060181a89a834e3d9caf

## Changes from source
- `DESIGN.md`: copied verbatim (Space Mono display, §2 primary #37F712 on
  surface #E7E5E4, §4 compact density).
- `tokens.css`: REGENERATED — the upstream open-design tokens.css declared a
  black-on-white monochrome palette contradicting its own DESIGN.md §2
  (matrix green #37F712, warm gray #E7E5E4).
- Contrast fix (mandatory gate): DESIGN.md Text #78716B on #E7E5E4 is 3.82:1
  (sub-AA) and #37F712 needs dark text.  Text darkened to #1c1917 (--fg, 12.5:1)
  / #44403c (--fg-2, 6.4:1); surface + green accent kept.  Every pair >= 4.5:1.
- `reference.html`: new brand-neutral terminal-styled one-pager ("vtxlab"),
  token-driven.
