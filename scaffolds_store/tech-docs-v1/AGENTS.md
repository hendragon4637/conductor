# AGENTS.md — Technical Documentation Standard (v1)

## Structure [researched — Diátaxis, the most widely adopted framework]
```
docs/
  tutorials/     # learning-oriented: a beginner completes something end to end
  how-to/        # task-oriented: steps to achieve one specific goal
  reference/     # information-oriented: API/config/CLI facts, complete and dry
  explanation/   # understanding-oriented: why it works this way, trade-offs
  index.md       # entry point + navigation to the four sections
.markdownlint.json
.vale.ini        # prose style (optional but preferred)
RUN.md           # how to lint/build/preview the docs
```
Put every page in exactly ONE Diátaxis section. If a page mixes teaching and reference, split it —
mixing is the most common documentation failure. Avoid a flat file dump.

## Page rules
- **Front matter on every page** [researched — docs-as-code convention]:
```markdown
---
title: Authentication Guide
description: How to authenticate API requests using API keys or OAuth 2.0.
last_updated: 2026-07-20
---
```
- One H1 per page, never skip heading levels (H2 → H4 is a lint error). [researched — markdownlint]
- Fenced code blocks ALWAYS carry a language tag (```bash, ```python) — never bare ```. [researched]
- Descriptive link text ("see the auth guide"), never "click here" / bare URLs.
- Relative links between docs (`../reference/api.md`), so the link checker can verify them.
- Headings must make sense out of context (search + screen readers read them isolated). [researched]
- Prose: active voice, second person ("you"), present tense. Short sentences. No marketing language.
- Every documented command/snippet must be copy-pasteable and correct as written — no pseudo-commands,
  no `<your-value-here>` without stating where to get it.

## Verification (docs are tested like code) [researched — docs-as-code CI practice]
- `markdownlint docs/**/*.md` — structure/syntax clean.
- Link check — every internal relative link resolves; no broken anchors.
- Front-matter check — every page has title + description + last_updated.
- `vale docs/` when a style config is present (warnings acceptable, errors are not).
- Run `bash gates.sh` before reporting completion.

## Process
- Update `docs/index.md` navigation when adding a page — an unlinked page is an incomplete deliverable.
- Prefer editing an existing page over adding a near-duplicate.
- Do not invent product behavior: document only what the code/spec in this workspace actually does.
  If something is unknown, write a TODO with the specific open question — never guess. [synthesis, important]
- No scope expansion beyond the requested pages.

---
**Provenance:** Diátaxis four-section structure, docs-as-code testing (markdownlint + link check + Vale),
front matter, code-fence language tags, heading rules, descriptive links = [researched] (Diátaxis adoption
reporting, GitBook/Mintlify docs guides, GitLab docs-testing pipeline, markdown docs-as-code guides 2026).
Active voice/second person = [consensus] technical-writing style. "Never invent behavior — write a TODO
with the open question" + index-navigation rule = [synthesis] (rationale: hallucinated docs are the key
failure mode for LLM-written documentation; gives the judge a concrete honesty criterion).
