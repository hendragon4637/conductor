# __PROJECT__ — Design System (variant: __VARIANT__)

> The authoritative brand contract. tokens.css is derived from this file; if
> they ever disagree, THIS file wins and tokens.css must be regenerated.

## Variant
- `__VARIANT__` — see `variants/__VARIANT__/DESIGN.md` for the full source
  contract and `variants/__VARIANT__/SOURCE.md` for provenance.
- Copy `variants/__VARIANT__/tokens.css` to the workspace root before building.

## Palette
- Background: `var(--bg)`
- Surface: `var(--surface)`
- Text: `var(--fg)` / `var(--fg-2)` / `var(--muted)`
- Border: `var(--border)` / `var(--border-soft)`
- Accent: `var(--accent)` / `var(--accent-hover)`
- Semantic: `var(--success)` / `var(--warn)` / `var(--danger)`

## Type Scale
- Display: `var(--text-4xl)` / 700, tight
- Section: `var(--text-3xl)` / 600
- Card: `var(--text-2xl)` / 600
- Feature: `var(--text-xl)` / 600
- Body: `var(--text-base)` / 400
- Small: `var(--text-xs)` / 400
- Mono labels: `var(--font-mono)`

## Spacing
- `var(--space-1)` … `var(--space-12)` — multiples of the base unit
- Section vertical rhythm: `var(--section-y-desktop|tablet|phone)`

## Radius / Elevation
- `var(--radius-sm|md|lg|pill)`, `var(--elev-flat|ring|raised)`

## Voice
- Tone: per variant DESIGN.md (see `variants/__VARIANT__/DESIGN.md` §8)
- Pronoun conventions per variant; keep microcopy action-oriented