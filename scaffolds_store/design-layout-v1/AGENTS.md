# Design Layout Conventions

## Tooling
- DESIGN.md = the brand contract (color, type, spacing, voice)
- Every artifact shaped by DESIGN.md tokens — no ad-hoc values
- open-design (OD) skills + CLI for rendering

## The Workflow
1. Clarify — restate brief: audience, purpose, format, constraints
2. Template — start from skill assets, never empty canvas
3. Populate — real content; apply DESIGN.md tokens only
4. Self-check — against brief AND DESIGN.md (hierarchy, contrast, spacing)
5. Preview — render (HTML / PDF / PNG)
6. Refine — targeted edits from critique, never full regeneration

## Project Structure
- `DESIGN.md` — brand contract (tokens, palette, type, spacing)
- `brief/BRIEF.md` — clarified brief with self-check checklist
- `work/` — HTML/CSS sources
- `exports/` — deliverables (.html always; .pdf/.pptx as brief demands)

## Style Rules
- Only DESIGN.md tokens for color/type/spacing
- Semantic HTML, real CSS — no screenshot-of-designs
- Contrast ≥ WCAG AA for text; alt text on images
- Every export listed in RUN.md VIEW with description

## Completion Check
Before marking a node complete, run `bash gates.sh` from the workspace root.
The script must exit 0 and print "ALL GATES GREEN".
