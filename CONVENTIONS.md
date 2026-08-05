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
- L4 runs for every completed run (no `needs_usage_sim` gating — runs unconditionally)
- Scenarios are generated from `goal+spec` BEFORE the agent session via `generate_scenarios()` in `l4_scenarios.py`
- Scenarios are intent-level only (as_a, wants, success_looks_like) — no steps, the agent must figure out HOW from RUN.md
- Scenarios written to `l4_scratch/scenarios.json` in the L4 workspace before agent spawn
- Agent writes `l4_scratch/report.json` as a structured `L4Report` (Pydantic validated)
- L4 runs stored in the `runs` table with `kind='l4'` and `parent_run_id=<parent_run_id>`
- L4 runs are EXCLUDED from the active-run-per-project constraint (`idx_runs_active_project` filtered)
- L4 runs that fail must NOT emit `run.failed` to the event bus (no intake noise from L4 infra failures)
- `report_consistent()` runs 6 deterministic checks; failure = structural failure, report never published (restored guide 06.3/06.4 gate; was temporarily loosened for e2e testing)
- `resolve_where_paths()` ensures all `where` paths in findings exist in the worktree
- Empty findings with `verdict=pass` is a complete correct report — never penalize clean sessions
- 3-gate publish rule: structural=ok AND verdict∈{partial,fail} AND any finding severity≥floor (default: medium) — all three required in `_on_l4_observed` (restored 2026-07-31; the temporary parse-only emit is removed). A parsed-but-inconsistent report never publishes.
- L4 structural failures get ONE bounded retry: a preamble naming the defect is sent to the existing AionUi conversation and a fresh attempt-2 node_session is spawned via NodeSpawned; a second failure records and continues (guide 06.3). The workspace is NOT cleaned between attempts.
- L4 runs always carry `merge_status='skipped'` (they never merge — the `'merged'` default would corrupt the blocked-merge queue)
- `on_run_completed()` calls `run_l4_phase()` which is spawn-only: generates scenarios, creates L4 run + node_session (`role='l4'`, `backend='opencode'`), spawns AionUi, emits NodeSpawned, and RETURNS immediately (no polling)
- L4 completion is watcher-observed: watcher-svc polls the L4 worktree with `role='l4'` config (60s settle, 5 stable polls via `_SETTLE_S_L4` / `_STABLE_POLLS_L4` env vars)
- Watcher emits `node.observed`; evaluator-svc `on_node_observed()` routes `role='l4'` to `_on_l4_observed()` which validates report and emits `l4.findings`
- `_prepare_l4_workspace()` does `git init` + initial commit so watcher's `_git_state_signature()` can track file changes
- Workspace persists until `_on_l4_observed()` cleans up — do NOT clean from `on_run_completed()` if NodeSpawned was emitted
- L4 `opencode.json` denies edits except `l4_scratch/**`, denies git/destructive/sudo, denies webfetch/websearch
- L4 agent must be observational-only: use the product, do not inspect/edit/fix source, write only to `l4_scratch/`
- Max 2 adhoc scenarios per session (agent-invented scenarios beyond the seeded set)
- Every finding must have a `scenario_id` linking it to a scenario attempt, and resolving `where` paths
- Findings below severity floor are retained in the JSONB `l4_report` but not emitted as `l4.findings` events
- Legacy `l4_standalone`, `l4_acceptance`, `l4_status`, `l4_reason` columns on `runs` table kept as deprecated
- `Run` SQLAlchemy model in `shared/models.py` has `project_id = Column(String, nullable=False)` (was missing, added 2026-07-28)
- `emit()` for L4 findings uses `shared.outbox.emit()` (not the `services.evaluator.main` module — avoids import cycle)
- Manual override via `POST /l4/manual` — validates same `L4Report` model + `report_consistent()`, uses `labeled_by="human"`
- L4 agent config at `agent_configs/l4-persona.yaml` — model_preference read at runtime via `_resolve_l4_model()`
- L4 runs in isolated copy under `workspace/l4_runs/<run_id>/` prepared by `_prepare_l4_workspace()`
- L4 execution model is agent-driven (guide 05, Option B, 2026-07-31 — documented deviation): an LLM persona drives the product black-box; the deterministic run_block/driver model (guide 05.1/05.2) is NOT implemented
- L4 install/setup commands come from the project manifest `.conductor/workspace.json` (`components[].commands.setup`), never parsed from RUN.md; each setup runs in its component's `subdir` in one `set -e` shell; assembly projects (root `workspace.json` with `services`) skip local install
- `_prepare_l4_workspace()` persists the source signature to `l4_scratch/source_baseline.json`; `_on_l4_observed()` verifies source unchanged before publishing — a mutated source fails the run as `l4_status='run_failed'` and emits no findings

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
- For the OpenCode backend executor family, `nvidia/openai/gpt-oss-120b` is the default/fallback model when a node-level preference is absent

## Database access
- Runtime code should resolve PostgreSQL through `DATABASE_URL`; avoid duplicating host/database names in application logic
- Manual inspection may use `docker exec postgres psql -U aipc -d aipc_conductor`, but the app-side source of truth remains `DATABASE_URL`

## Microservice event bus (services/)
- Each microservice has its own `.env` in `services/<name>/.env` sourced at startup
- The outbox relay loop (`EventBus.relay_loop()`) MUST use its own pika `BlockingConnection` — never share the consumer channel. Shared channels between consumer and relay threads corrupt the AMQP frame stream (`frame_too_large` / unexpected frame errors on RabbitMQ).
- The relay loop MUST reconnect when the channel is closed (RabbitMQ heartbeat timeout closes idle channels). Check `channel.is_closed` each cycle and recreate the connection+channel.
- Events are emitted via `shared.outbox.emit(session, event)` INSIDE the handler's DB transaction. The relay loop publishes them asynchronously.
- Consumers deduplicate via `processed_events` table using `dedupe_key()` (event_key = `{run_id|plan_id|node_session_id}:{routing_key}`).
- RabbitMQ topology: topic exchange `conductor.events`, durable queues per service (`planner.q`, `executor.q`, `watcher.q`, `evaluator.q`, `intake.q`), bindings in `BINDINGS` dict in `shared/bus.py`.
- When consuming monolith functions (e.g. `launch_run()`) that use UPSERT, verify all columns are in the `ON CONFLICT ... DO UPDATE SET` clause. The monolith's `save_node_session()` omits `worktree` — patch node_sessions directly after calling `launch_run()`.
- Gate outcome values: evaluator emits `gate_outcome=done` on pass, but executor's `_handle_gate_evaluated` switches on `pass`/`fail`. Non-match falls through to "advance next node" — no finalize or quarantine fires. Both `done` and `pass`, and `fail` and `failed` are now handled.
- The monolith watcher (`get_watcher()`) is also initialized inside executor-svc when `launch_run()` calls it. This creates a separate watcher polling in the executor process alongside the microservice watcher-svc — harmless but creates duplicate state.
- NEVER have multiple pika consumers on the same queue. RabbitMQ round-robins messages across consumers regardless of routing key. Use a SINGLE dispatcher consumer that routes by event type (detected from payload fields).
- Before calling `finalize_success()`, always auto-commit the worktree via `git add -A && git commit`. The agent writes files to the worktree but never commits them; the merge requires committed changes.
- L4 handler consumes `run.completed` events — the `BINDINGS` dict in `shared/bus.py` must have `"run.completed"` in `evaluator.q` list
- L4 now uses watcher-observed node_sessions (`role='l4'`): evaluator spawns L4 node_session, emits NodeSpawned, watcher polls, watcher emits node.observed, evaluator's `on_node_observed()` routes `role='l4'` to `_on_l4_observed()` — a separate handler path from the execution node L1/L2 gate pipeline
- Ratchet handler consumes `ratchet.trigger` events — same binding list addition pattern
- When adding new event consumers, add the routing key to BOTH the `BINDINGS` dict AND the microservice's dispatcher routing logic — RabbitMQ bindings alone don't route to handlers
- The evaluator dispatcher routes by inspecting payload fields (e.g., `event_type`), not by routing key — maintain this pattern for new consumers
- RabbitMQ `StreamLostError: ConnectionResetError(104)` can occur during high-throughput relay + publish. The relay loop must reconnect on channel close. The consumer thread reconnection uses the same loop in `bus.py`.
- A background outbox relay can crash under connection pressure; the relay reconnect loop logs "Relay channel closed — reconnecting" and re-establishes.
- **Intake-svc** (`services/intake/`) follows the same pattern as other microservices: FastAPI app on `:8095`, `.env` at `services/intake/.env`, shared outbox + deduplication, dispatcher routes by `event_type` field. Intake does NOT emit events (findings sink/service).
- When adding new event consumers to intake-svc, add the routing key to `BINDINGS["intake.q"]` AND implement a handler branch in `services/intake/main.py`'s `_dispatch_event()`.

## Capability family (JSONB array)
- `capabilities.family` is a JSONB array of strings, not a single TEXT value
- Multi-family capabilities use `["software", "design"]` to match multiple domain pre-filters
- Queries use `family ?| %s::text[]` for overlap matching — never `family = %s` (will not work)
- The GIN index `idx_cap_family_gin` supports efficient `?|` lookups
- `DOMAIN_TO_FAMILY` values in `selector.py` are `list[str]`, not `str`; use `["design", "creative"]` for backward-compatible domain→family mapping
- `_FALLBACK_CAPS` in `registry.py` stores `family` as a list; fallback matching uses `any(f in c["family"] for f in families)`

## Stress test data
- Generated goals in `stress_goals` use `source='generated'` and have unique `sg-` prefixed UUID IDs
- Seed scripts use `source='example-generated'` for capabilities and agent_configs created during stress test setup
- `scripts/gen_stress_goals.py` reads `LITELLM_KEY_PLANNING` as fallback for `LITELLM_GATEWAY_KEY` when auth is needed
- Stress test capabilities follow the same family-array convention as production capabilities
- Content Studio caps with unrealizable tools (`image_gen`, `audio_gen`) are expected to honestly fail realizability checks — no silent skipping

## Skills & profiles (agent import pipeline)
- Imported profiles use `source='imported'` in `agent_configs`; hand-written use `source='hand'`
- New harness renderers: subclass `HarnessRenderer`, set `name`, implement `render_agent()`/`render_skill()`, register via `register()` in `backend/skills.py`
- Skill layers: **global** skills (`~/.config/opencode/skills/`) available to all runs; **worktree** skills (`.opencode/skills/` per worktree) scoped to node capabilities via `capability_skills` mapping
- `install_worktree_skills()` is called pre-spawn in `spawn_node_team()` — never call it separately
- Realizability checks: `check_capability_realizability()` flags capability tools unsupported by a harness
- Collision guard: OMO reserved names + duplicate agent_config_ids get `imp-` prefix during import
- All imported agents default to `backend_targets=["opencode"]`; extend when adding a new harness
- Skill catalog import: `scripts/import_profiles.py --skills-only --pin`. Processes awesome-agent-skills README links in interleaved batches of 30: sequential fetch (1 worker, 2s gap between requests, exponential backoff on 429) → LLM batch classify → DB upsert → 60s cooldown → next batch
- Per-skill folders are stored in `skills_store/<skill_id>/` with `store_path` in the DB. The renderer copies these folders to harness-specific skill dirs (not individual files).
- Scripts detection uses content-based regex (`scripts/[\w.\-/]+`) on the fetched SKILL.md body, not GitHub API (avoids rate limits)
- Resume partial imports with `--start-batch N` (1-indexed batch number)

## Planner (meta-planner)
- `NODE_BRIEF.md` holds static reference material (role, steps, rules, schemas, roster) — written by `_write_planner_opencode_json()` at worktree setup
- `planning_brief()` builds ONLY dynamic content (goal/spec/quality_intent/worktree path, domain-filtered capabilities/dimensions); references NODE_BRIEF.md for static content
- `retry_brief()` calls thin `planning_brief()` + prepends ✓/FIX block — never regenerates static content
- `_schema_text()` is called once and cached; duplicate calls have been removed
- `_build_static_brief()` in `harness_worktree.py` composes the static brief from DB sys_prompt + role + steps + rules + schemas + roster
- `save_plan()` in `backend/planning/store.py` auto-creates a project row (`INSERT INTO projects ... ON CONFLICT DO NOTHING`) before persisting the plan — callers (``/goal``, ``/clarify``, ``/ratify``) never need to pre-seed projects

## Run constraint (project_id)
- Every run has a required `project_id` column (FK to plans.project_id, NOT NULL)
- Partial unique index `idx_runs_active_project` on `runs(project_id)` WHERE state NOT IN ('done','failed','cancelled','planning') — allows multiple planning attempts before ratification but only one active run per project
- `get_active_run_for_project(project_id)` returns any run in a non-terminal state (excludes done/failed/cancelled)
- `save_run()` raises `ValueError` if `project_id` is missing from the run dict (defense-in-depth)
- `/goal` endpoint checks for active project run (409) before invoking planner LangGraph
- `/ratify` endpoint checks for active project run (409) before creating execution run

## Plan evaluator (gate & judge)
- `call_llm_structured()` accepts optional `role` param (default `"meta_planner"`) and `include_raw` param (default `False`)
- `role="l2_judge"` routes through the gateway to the JUDGE model group — independent from the meta-planner generation model (`deepseek-planning`)
- `include_raw=True` returns `(parsed, raw_text)` tuple; the raw response is stored in `PlanL2Result.raw_response` and persisted to `plan_l2_raw_response` TEXT column
- `plan_l2_raw_response` is for observability only — never fed into the retry brief
- Gate results (`gate_outcome`, `l2_score`, `feedback`, `l2_feedback`) are persisted to `node_sessions` on EVERY gate decision (both ratify and revise) in `_on_node_observed_planning()`
- `update_plan_gate_result()` is called in the FAILURE path before retry, persisting `plan_goal_review` + `l2_judgments` + `raw_response` mid-retry for observability
- **RAW ERRORS merge**: `retry_brief()` in `harness_worktree.py` calls `_extract_fix_files_from_raw_errors()` to parse `node-NNN:` patterns from staffing error lines and merge those file paths into `fix_files`. This ensures `FIX THESE` includes scoped references even when structure is clean but GATE fails.
- **Feedback text preservation**: `gate_plan()` MUST include the LLM's `what`/`why`/`how` text in the feedback string. The `[feedback degraded]` marker is an appendix qualifier, NOT a replacement — never emit only `[feedback degraded]`.
- **Sequential DAGs**: Nodes MUST be sequential (each depends on previous). The L2 judge prompt prohibits flagging sequential dependencies as unnecessary. Parallel DAGs are not allowed.
- **Domain-appropriate measurable rubric**: The `measurable` rubric item must accept rubric-based quality checks for design/visual domains (`visual_design`, `design_layout`) — not all domains have deterministic success criteria.

## Git
- Atomic commits with clear messages
- Never commit .env, *.db, node_modules/, __pycache__/
- No force push to main

## Worksystem (File 10, guide 10.x)
- Worksystem repos live under `worksystem/repos/<system_id>/` (env `WORKSYSTEM_ROOT`); master worktrees under `workspace/` (env `WORKSPACE_ROOT`). Both dirs are gitignored — derived state, never committed.
- Publish is derived-state regeneration: member files copied from `publish_manifest`, `index.json` + `compose.yml` regenerated via `refresh_index()`/`render_compose()` (never patched), then one git commit. `_source.json` records project_id/sha/image_tag/published_at provenance.
- Publish-on-merge runs inside a `pg_advisory_lock` keyed `worksystem.publish.<system_id>`; failure marks `publish_status='stale'` and must never fail or block the run.
- Publish skips when the project has no system (`system_of()` → None) or is an assembly composer (`project_kind() == 'assembly'`).
- System L4 runs off a git worktree snapshot (detached HEAD, tag `l4/run-<run_id>`), never off the live worksystem repo — the `_source.json` sha is what staleness tagging compares against master.
- Worksystem opencode.json permits edits (compose.yml edit is the adjustment signal) but denies `git *`, `sudo *`, `rm -rf *`, webfetch, websearch.
- Staleness: `tag_possibly_stale()` marks findings about members whose published state lags master; intake drops `possibly_stale` findings. Recurring adjustments (`same_adjustment_in_last_n_runs`, window 3) escalate a finding even on pass verdicts.
- `_FILE_FLATTEN` maps `.conductor/workspace.json` → member `workspace.json`; other files copy by basename. Artifacts >2 MB (`ARTIFACT_CAP_BYTES`) become `.ref` pointers, not copies.

## References store
- `has_references(project_id)` is the single guard: returns True only when `references/<project_id>/` exists AND has a `README.md`. All reference wiring is conditional on it — no store = silent no-op, never an error.
- Copy is one-way: `copy_references()` copies `references/<project_id>/` → worktree `.conductor/references/` in BOTH planning (`create_planning_worktree`) and execution (`assemble_for_spawn`) worktrees. `.git` is always excluded (`shutil.ignore_patterns(".git")`) — context, not history.
- References are gitignored, never committed: execution worktrees get `.conductor/references/` via `worktree_gitignore_lines()` (`.conductor/` is in `INFRA_EXCLUDES`); planning worktrees call `gitignore_references()` because `_unignore_plan_dotdir()` un-ignores `.conductor/` there.
- Read-only enforcement: planning `opencode.json` always carries `"edit": {"**": "allow", ".conductor/references/**": "deny"}`; execution applies `_deny_references_edit()` ONLY when `has_references()` so reference-less projects keep identical permissions.
- `planning_brief()` appends the conditional `REFERENCES (read-only context — do not edit):` block listing each README.md relative path only when `.conductor/references/` exists in the worktree.
- New stores are seeded by humans at `references/<project_id>/README.md`; there is no pipeline that creates them.
- Evaluator guards share one source of truth: `READ_ONLY_CONTEXT_PATHS` in `contracts/paths.py` (contains `deps/` + `.conductor/references/`). `run_plan_l1()` Check 7 rejects nodes whose task/success/deliverables reference these paths, and the L2 gate drops judge feedback mentioning them — both must iterate the shared tuple, never hardcode a path.
