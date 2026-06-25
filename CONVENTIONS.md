# Conductor Coding Conventions

## Python
- Type hints required on all public functions and methods
- Use `from __future__ import annotations` in new files
- Prefer explicit `dict(zip(keys, row))` over sqlite3.Row / DictCursor for DB row access
- Error handling: catch specific exceptions, log via `logger.exception(...)`, never bare `except:`
- Thread-safety: use `threading.Lock` for shared state, never unprotected globals
- JSON: use `json.dumps()` / `json.loads()`, never eval or manual serialization

## Migration files
- Numbered sequentially (011, 012, ...) with a descriptive suffix
- Idempotent via `IF NOT EXISTS` / `IF EXISTS` where possible
- Run manually via psql into `aipc_conductor` DB

## API design (FastAPI)
- Prefix routes under `/api/` (e.g., `/api/chat/threads`)
- Use Pydantic models for request/response bodies
- Return standard HTTP codes: 200/201 for success, 400 for validation, 404 for not found, 500 for server error
- Keep controllers thin; business logic in separate modules

## Watcher
- Poll-based, not event-driven (simpler, more robust)
- SessionState holds all mutable state per worktree+node
- Two polling signals: git diff (file changes) + DB query (cheap query signature)
- "Terminal" = stable (no change) for >= 2 consecutive polls + settle_s seconds
- No heuristic for terminal content type — stability is the signal
- `backend.watcher` logger MUST have a `StreamHandler` at module init (Python loggers without handlers silently swallow output; uvicorn does not set up handlers for `backend.watcher` automatically)
- Add `[PRINT]`-prefixed `print(..., flush=True)` calls at all verdict transitions and evaluator gate decision points for debug visibility regardless of logger configuration

## Evaluator (meta-evaluator)
- Evaluator sits between watcher "done" verdict and node commit — NEVER modify watcher for evaluation
- L1 deterministic checks run first (cheap, no LLM); L2 rubric judge only if L1 passes
- Checks are generated at decompose time via meta-planner LLM (`check_generator.py`) or legacy `generate_checks()`, ratified by human at plan approval
- L1 checks are selected by ID from `CANONICAL_L1_PRESETS` only — never generated, reworded, or invented by the LLM
- L2 checks carry provenance: `preset` (unmodified), `preset_adapted` (adapted per-node), or `human_intent` (created from quality_intent)
- LLM outputs L1 `check_cmd` as the ID string (placeholder); system resolves to actual command via `_resolve_l1_checks()`
- Unknown/hallucinated L1 IDs are dropped with warning by `_resolve_l1_checks()`
- Plan evaluator (`run_plan_l1()` check #6) validates L1 IDs against canonical pool when `use_meta_planner=true`
- Heuristic `generate_checks()` in `generate.py` runs only on legacy path (without `use_meta_planner=true`) — never regenerates LLM output
- `_persist_plan_dag()` converts existing LLM output to Check objects directly; does NOT overwrite with heuristic generation
- `Check.tier` is a computed `@property` (`type=deterministic`→`L1`, `type=rubric`→`L2`), never set directly
- Remediation nodes reuse `decompose_or_update("append_node")` lifecycle — no new orchestration
- L1 runs shell commands in the node worktree; exit 0 = pass
- L1 checks are FORBIDDEN from containing runtime signals (curl, localhost, 127.0.0.1, http://, uvicorn) — these belong to higher layers (L4). `validate_checks()` rejects leaked checks at generation time.
- L2 input is size-guarded (`L2_MAX_INPUT_CHARS=24000`): oversize → flag-fail (score=0, oversize=True), no silent truncation
- Rubrics come from preset library, never zero-shot generated
- Evaluator gate fail-open: if L1 itself errors, node still commits (never block on evaluator infra)
- L3 (meta-eval) runs out-of-band, NOT in the hot path — scheduled periodically (e.g. weekly)
- L3 golden set is FROZEN and human-only; nothing in the pipeline writes `golden_set` automatically
- L3 jury must use ≥2 different model families; single-family fallback documents the limitation in the `note` field
- L3 drift → rubric refinement proposals are QUEUED with `status='pending'`, never auto-applied
- L4 runs conditionally only when the product has a user-facing surface (`needs_usage_sim`)
- L4 executes behaviors as HTTP requests against a running product server (black-box, no source reading)
- Remediation carries verbal feedback from the gate failure: `build_feedback()` builds structured `{failed_checks, reflection}` from the gate decision; `build_remediation_brief()` builds the fix-forward prompt (original goal + failed checks + what to fix + "FIX IT — do NOT start over")
- Remediation attempt cap is 2 (1 original + 1 retry); `remediation_of` links retry to its predecessor
- L4 produces structured friction scores per dimension; report is surfaced for human review — NEVER auto-decides feature direction
- L4 `L4Report` has no `auto_apply` or `decision` field — it carries observations only

## L3 calibration
- `calibrate(node_type)` re-scores all frozen golden artifacts for that node_type via the L2 judge, computes MAE and item-level agreement, then upserts `judge_trust`. Never modifies the golden set.
- `count_golden(node_type, split=None)` returns total or split-specific counts from `golden_set`.
- `get_judge_trust(node_type)` returns the current `judge_trust` row for a node_type — used by `assert_ready()` before ratchet experiments.
- Calibration runs out-of-band, scheduled periodically (weekly). It is NOT in the hot path.
- Each `golden_set` row has `frozen=TRUE` — the ratchet may never set this to FALSE.
- Use `calibrate()`'s `CalibrationReport.items` list to surface per-item drift to humans.

## Ratchet
- Ratchet consumes the evaluator's `goal_review` Langfuse score, NOT the watcher verdict — experiment scoring uses `run_l2()`
- Frozen-boundary enforcement: ratchet may ONLY mutate probabilistic artifacts (skill, agents_md, prompt, rubric). Structural markers (`:` / `=`) distinguish config values from natural language mentions
- Scope gating: global-scope winners (domain=backend/general) are QUEUED for human approval; project-scope winners may auto-apply
- Held-out validation: candidate mutations must not regress on held-out tasks — overfitting to mining set causes revert
- Failure mining reads Langfuse `goal_review` score comments (format: `check_id: FAIL (explanation)`) to cluster recurring rubric failures
- `assert_ready(agent_config_id, node_type)` must pass before `run_experiment()` — raises `RuntimeError` if judge not trusted, heldout < 5, or recent scores empty
- `reject_if_frozen(target)` raises `ValueError` if the target field is in the frozen set — call before any mutation write
- `propose_mutation(failures)` returns a minimal system_prompt edit targeting the mined failure cluster — never touches frozen fields
- `validate_on_heldout(agent_config_id, node_type, candidate)` runs REAL L2 judge calls against the held-out split — not a proxy
- `run_experiment(agent_config_id, node_type)` is the main loop: mine → propose → validate → apply-or-queue → record
- Global-scope mutations (domain=backend/general) are written with `status='pending'` in `experiments` table; project-scope get `status='applied'`

## Memory ↔ Evaluator integration
- Read direction: call `ground_checks_with_memory(task, project, agent)` BEFORE `generate_checks()` to inject memory-grounded rubric items from Neo4j product memory
- Write direction: call `capture_evaluator_findings()` AFTER gate decisions to persist failing L1/L2 items as MemoryFact nodes at session scope
- Meta tier: call `ground_meta_evaluation(plan_description)` during conductor-self scoring to flag plans violating locked DECISIONS.md invariants
- All memory functions degrace gracefully — return empty list / 0 if Neo4j is unreachable or memory module absent
- NEVER read from or write to the golden set via memory paths; promotion to global scope stays human-gated

## MCP server conventions
- Conductor MCP server runs on `127.0.0.1:8092`; Obsidian vault MCP on `127.0.0.1:8093`
- Both use SSE transport for remote access — never stdio (client is on a different machine)
- Bearer token auth via env vars (`CONDUCTOR_MCP_TOKEN`, `OBSIDIAN_MCP_TOKEN`)
- Token auth uses `app.add_middleware(TokenCheckMiddleware)` — never Starlette-reconstruction (Router has no `on_startup`)
- MCP tools are read + pending-create only: never expose approve/spawn/delete/cancel
- Tool names use hyphens (`-`) as namespace separators, never dots (`.`). E.g., `conductor-create_plan`, `conductor-refine_plan`, `obsidian-read_note`
- `conductor-create_plan` and `conductor-refine_plan` return pending plans only — ratification happens in the Conductor UI
- Proposals sent via MCP are validated by Conductor's existing plan spec before persistence

## Development environment
- Project root: `/opt/aipc/conductor` (always use this path, never guess)
- Python venv: `/opt/aipc/conductor/.venv/bin/python` (managed via `uv`)
- Database: PostgreSQL via `DATABASE_URL` env var (required by orchestration modules)
- UI: React app at `ui/` — run `npm run dev` from `/opt/aipc/conductor/ui/`
- Backend API: `backend.main:app` on `127.0.0.1:8090`
- Watcher lifecycle is owned by `backend.main:app`; do not run a second standalone watcher for normal e2e/backend operation

## Hermes adapter (execution backend)
- Hermes Agent (v0.16.0) is a second execution backend alongside AionUi — same tier, same Conductor control plane
- Integration via HTTP API at `http://127.0.0.1:8642/v1` with bearer token auth (`HERMES_API_KEY`)
- Hermes runs under Docker sandbox (terminal.backend: docker) with Conductor worktree mounted at `/workspace`
- Conductor sends ONE goal per node — Hermes self-decomposes and routes to its own subagents internally
- Never reach into Hermes's internal skill store (observe-only coupling)
- Hermes's session store is SQLite at `~/.hermes/state.db` (watcher source for Langfuse ingestion)

## Memory structure
- Meta memory files (`DECISIONS.md`, `COMMANDS.md`, `CONVENTIONS.md`, `GLOSSARY.md`) are in repo root, hand-authored
- `.memory/` directory contains generated artifacts (repomix snapshot, assemble.sh)
- `AGENTS.md` is generated, never hand-edited; run `assemble.sh` to rebuild
- Repomix is kept in two forms: one repo-local snapshot (`.memory/snapshots/conductor_repomix.md`) and one Obsidian-friendly chunked export (`/home/aipc/conductor-notes/research/repomix/`)
- Product memory lives in Neo4j scoped by group_id

## Agent config (model_preference)
- Agent config YAML files in `agent_configs/` are the **authoritative** source for model selection
- The `model_preference` field in the YAML is passed directly to AionUi as `current_model_id`
- The old minimax→opencode override in `spawn.py` (`_normalize_model`) has been removed — do not re-add
- Re-sync via `uv run python scripts/bootstrap_first_config.py` after editing any YAML in `agent_configs/`
- For the OpenCode backend executor family, `nvidia/gpt-oss-120b` is the default/fallback model when a node-level preference is absent

## Database access
- Runtime code should resolve PostgreSQL through `DATABASE_URL`; avoid duplicating host/database names in application logic
- Manual inspection may use `docker exec postgres psql -U aipc -d aipc_conductor`, but the app-side source of truth remains `DATABASE_URL`

## Git
- Atomic commits with clear messages
- Never commit .env, *.db, node_modules/, __pycache__/
- No force push to main
