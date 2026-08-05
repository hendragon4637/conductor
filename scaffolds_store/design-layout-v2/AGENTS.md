# Design Layout Conventions (v2 — variant system)

## Tooling
- DESIGN.md = the brand contract (color, type, spacing, voice) — AUTHORITATIVE.
- tokens.css = the machine-readable token set derived from DESIGN.md. The ONLY
  file (besides DESIGN.md) that may hold raw hex/rgb literals. Every artifact
  uses `var(--token)` from it — no ad-hoc values.
- `scripts/check_tokens.py` = the token-conformance gate (runs at curation AND
  at node completion). See README in scripts/ for usage.
- open-design (OD) skills + CLI for rendering.

## Variants (5 curated)
Selected variant is pinned in `.conductor/workspace.json` under
`components[].variant` and its folder copied from `variants/<name>/` into the
worktree. Choose by brief intent; selection is explicit > single > LLM.

| name | brand persona | primary accent |
|------|---------------|----------------|
| technical-dense | dense, data-rich, hashicorp-style | red |
| editorial-serif | long-form, editorial, serif | green |
| soft-clay | friendly, rounded, warm cream | matcha green |
| brutalism | bold, concrete, hard black lines | terracotta |
| mono | matrix, high-contrast, monospace | green |

## The Workflow
1. Clarify — restate brief: audience, purpose, format, constraints
2. Variant — pick the closest variant; copy its `tokens.css` (never hand-edit
   unless redesigning the variant itself)
3. Template — start from skill assets, never empty canvas
   - DESIGN.md and work/tokens.css are your STARTING system. You may extend
     the palette (add a token) but not abandon it (introduce a literal).
   - Every new value goes in tokens.css first, then gets used via var(--…).
   - reference.html shows the system working. Read it before writing anything.
4. Populate — real content; apply DESIGN.md tokens only via var(--…)
5. Self-check — against brief AND DESIGN.md (hierarchy, contrast, spacing)
6. Preview — render (HTML / PDF / PNG)
7. Refine — targeted edits from critique, never full regeneration

## Project Structure
- `variants/<name>/` — curated library: DESIGN.md, tokens.css, reference.html,
  SOURCE.md (source path + SHA + changes). Copied, not modified, at seed time.
- `DESIGN.md` — active brand contract (tokens, palette, type, spacing)
- `tokens.css` — active token set (usually a copy of the variant's)
- `brief/BRIEF.md` — clarified brief with self-check checklist
- `work/` — HTML/CSS sources
- `exports/` — deliverables (.html always; .pdf/.pptx as brief demands)

## Style Rules
- Only DESIGN.md tokens for color/type/spacing; raw values live ONLY in
  tokens.css
- Semantic HTML, real CSS — no screenshot-of-designs
- Contrast ≥ WCAG AA (computed by check_tokens.py); alt text on images
- Every export listed in RUN.md VIEW with description

## Completion Check
Before marking a node complete, run `bash gates.sh` from the workspace root.
The script must exit 0 and print "ALL GATES GREEN".