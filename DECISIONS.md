# Locked Architecture Decisions (append-only)

## 2026-07-16 — save_plan auto-creates project row for unknown project_id

**Status**: ACTIVE

**Context**: The `/goal` and `/ratify` endpoints accept a `project_id` from the caller. If the project_id didn't exist in the `projects` table, `save_plan()` crashed with a PostgreSQL foreign-key violation since `plans.project_id` references `projects.project_id`. This forced callers to pre-seed projects before submitting goals — an unnecessary chore that broke when new project_ids were used.

**Decision**:
- `save_plan()` in `backend/planning/store.py` now runs `INSERT INTO projects ... ON CONFLICT (project_id) DO NOTHING` immediately before the plan INSERT
- The auto-created row gets `name = project_id` and `repo_path = /opt/aipc/conductor/workspace/{project_id}`
- If the project already exists, the upsert is a no-op — no error, no overwrite
- Covers all callers: `/goal`, `/clarify`, BYO-DAG path, and `/ratify` (which calls `save_run()` after plan persists)

**Rationale**: Eliminates a papercut where callers had to manually ensure the project row existed. The projects table is a lightweight registry — auto-creating a basic row is safe and avoids FK errors without adding ceremony.

**Trade-offs**: Auto-created projects have minimal metadata (no description, no system_prompt). Callers that want a richer project entry must still create it explicitly beforehand. The hard-coded `repo_path` convention may not suit all workflows, but matches existing patterns.

## 2026-07-01 — Planner graph conditional entry point (clarify → continue)

**Status**: ACTIVE

**Context**: The `/clarify` endpoint in the planner microservice returned `formulated` status after MetaGoal resolution but never continued the graph. Plans sat in `formulated` state without a DAG or gate evaluation indefinitely — the clarify→decompose→gate flow was broken.

**Decision**:
- Add `_route_entry()` conditional entry point to the planner LangGraph (`services/planner/graph.py`)
- `_route_entry()` checks `state["status"]` — routes to `"formulate"` when `status=="new"`, to `"inject"` when `status=="formulated"`
- `set_conditional_entry_point(_route_entry, ...)` replaces `set_entry_point("formulate")`
- The `/clarify` endpoint re-invokes the graph after `formulate_or_clarify()` returns the MetaGoal, so execution proceeds through `inject → decompose → select_capabilities → generate_checks → gate`

**Rationale**: Avoids adding a separate API endpoint for post-clarify continuation. The conditional entry point pattern is a standard LangGraph mechanism — minimal surface area, zero new API surface.

**Trade-offs**: The graph now has two entry paths, which adds complexity to graph reasoning. The `_route_entry()` function must remain stateless and simple to avoid routing bugs.

## 2026-07-01 — Planner graph remediation loop (feedback → decompose)

**Status**: ACTIVE

**Context**: When the planner gate fails, the plan was re-submitted from scratch — the meta-planner regenerated the entire DAG without any awareness of what failed. This wasted tokens and ignored the fix-forward opportunity.

**Decision**:
- `decompose()` in `backend/planning/meta_planner/decomposer.py` accepts new params: `feedback` and `prior_dag`
- The LLM decompose prompt has a new `{revision_block}` template slot that injects prior DAG JSON + gate feedback text + fix-forward instructions
- Fix-forward instructions say: "Keep the node structure that worked; only change what needs fixing. Do NOT regenerate from scratch."
- The planner graph's `_n_decompose()` passes `gate_feedback` and prior `dag` from `PlanState` on revision cycles
- Feedback goes to decompose, NOT to the capability selector — the selector is a downstream resolver that re-runs fresh on each revised DAG

**Rationale**: Blind retry wastes 2-3 LLM calls ($0.15-0.30 per revision) and ignores the specific failure signal. Targeted correction produces a better DAG in fewer iterations.

**Trade-offs**: The prompt grows ~400 tokens when revision block is present. The feedback text must be structured enough for the LLM to act on (not just "gate failed" but specific check failures).

## 2026-06-30 — Capability layer (selector, registry, check-gen)

**Status**: ACTIVE

**Context**: The planner had no structured capability awareness — agent_config selection was implicit in the decompose prompt, and there was no systematic way to evaluate whether selected capabilities could deliver the plan's requirements.

**Decision**:
- Introduce a three-module capability layer under `backend/planning/capability/`:
  - `selector.py` — Three-step process: domain family pre-filter → LLM capability selection → gap proposal path. The LLM selects from candidates within the matched domain family and can propose new capabilities when gaps exist.
  - `registry.py` — Central registry of all capabilities with family grouping. Exports `all_capabilities()`, `caps_in_family()`, `get_capability()`.
  - `checkgen.py` — Generates L1/L2 checks from capability quality dimensions at decompose time. L1 checks are deterministic existence/format checks; L2 checks are rubric-based quality evaluations.
- The staffing gate (`plan_evaluator.py`) evaluates whether selected capabilities meet plan requirements, using both L1 and L2 checks generated by checkgen.
- The planner graph's `select_capabilities` node invokes the selector; `generate_checks` node invokes checkgen

**Rationale**: Structured capability management enables traceable audit of "can this plan succeed?" before execution begins. The gap proposal path allows controlled expansion without central bottleneck.

**Trade-offs**: Adds ~400 lines of new code. The capability registry must be kept in sync with available agent_configs — stale registry entries cause false negatives in the staffing gate.

## 2026-06-30 — Microservice event-driven architecture (RabbitMQ + transactional outbox)
Status: ACTIVE
Decision: The monolith backend is decomposed into four microservices (planner, executor, watcher, evaluator) communicating via RabbitMQ topic exchange (`conductor.events`) with a transactional outbox for reliable delivery. Each service has its own FastAPI app, config (`.env`), and port. The outbox relay loop MUST use its own pika `BlockingConnection` — sharing the consumer channel between consumer and relay threads is NOT thread-safe and corrupts the AMQP frame stream.
Rationale: RabbitMQ provides durable async delivery without shared DB locks. The transactional outbox ensures exactly-once delivery: events are written atomically with the business DB transaction, and a background relay publishes them. Each service idempotently deduplicates via `processed_events`.
Consequence: The monolith's `launch_run()` is still imported by executor-svc verbatim but requires a post-call worktree patch (its `save_node_session` UPSERT omits the `worktree` column). Gate outcome values from evaluator (`done`) vs executor's handler switch (`pass`) are mismatched — `_handle_gate_evaluated` treats non-pass/non-fail outcomes as "advance next node".

## 2026-06-30 — Microservice ports and service boundaries
Status: ACTIVE
Decision: Five microservices run on consecutive ports:
- Planner-svc (:8093) — goal submission, clarification, ratification. Emits `plan.ratified`.
- Executor-svc (:8091) — consumes `plan.ratified`, calls `launch_run()` to spawn worktrees + AionUi teams. Emits `node.spawned`. Also consumes `gate.evaluated` (finalize/advance) and `node.remediate` (fix-forward retry).
- Watcher-svc (:8092) — consumes `node.spawned`, polls worktrees for stability (30s interval, 30s settle, 2 stable polls). Emits `node.observed`.
- Evaluator-svc (:8094) — consumes `node.observed`, runs L1 deterministic + L2 rubric judge. Emits `gate.evaluated` and optionally `node.remediate`.
- Intake-svc (:8095) — consumes `l4.findings`, `run.failed`, `plan.awaiting_clarification`, `plan.ratifiable`, `plan.failed`, `plan.rejected`. Stores findings in `intake_findings` table. Exposes `GET /api/intake/findings` and `POST /api/intake/findings/{id}/ack`.
Each service starts via `uv run uvicorn services.<name>.main:app --port <port>` sourcing its own `.env` from `services/<name>/.env`.

## 2026-07-02 — L4 persona simulation handler (on_run_completed)

**Status**: SUPERSEDED by 2026-07-28 L4 MVP2

**Context**: L4 (persona simulation) ran conditionally for user-facing products but had no event-driven trigger. The `run.completed` binding was added to evaluator queue's BINDINGS but the handler that consumed it lacked `db.commit()` — L4 scores were computed but never persisted.

**Decision**:
- Evaluator-svc consumes `run.completed` via `on_run_completed()` handler
- Handler spawns two AionUi ACP conversations (standalone + acceptance) via `aionui.create_conversation(preset_agent_type="acp")`
- Polls each for completion with 300s timeout (10s interval), looks for `type="text"` / `position="left"` messages
- Scores via keyword heuristic from agent's narrative report (counts pass/fail/status-code mentions)
- **Must call `db.commit()`** after writing `l4_standalone`, `l4_acceptance`, `l4_status`, `l4_reason` to the `runs` table
- Persona definitions are YAML files in `backend/evaluator/l4_persona/personas/`
- Agent config at `agent_configs/l4-persona.yaml` with `acp-browser` backend and `openmode`
- `RUN.md` in the worktree determines the product's base URL and start command

**Rationale**: AionUi ACP conversations are the standard spawn path (same mechanism as executor spawns node agents). This reuses existing infra — no new agent orchestration.

**Trade-offs**: Single-threaded consumer blocks for up to 10 minutes during L4 polling; other events queue up. The keyword scoring heuristic is fragile (false positives from "pass" in non-score contexts). Agent may refuse due to system instruction conflicts.

## 2026-07-28 — L4 MVP2: structured scenarios + L4Report + deterministic validation

**Status**: SUPERSEDED by 2026-07-28 L4 watcher-observed completion

**Context**: L4 v1 (dual persona: standalone+acceptance, free-text `l4_report.md`, deepeval keyword scoring) produced heuristic friction scores with no structured findings, no traceability to specific scenarios, and no downstream consumer. The dual-persona approach required two AionUi sessions per run (2× lookup cost). The keyword heuristic was fragile.

**Decision**:
- Replace dual-persona with a single L4 session per run, pre-loaded with intent-level scenarios
- Scenarios are generated from `goal+spec` BEFORE the agent session (one cheap LLM call), NOT authored as YAML
- Agent reads scenarios from `l4_scratch/scenarios.json`, attempts each, writes `l4_scratch/report.json`
- Report format is a Pydantic-validated `L4Report` (verdict + scenario_results + findings + observations)
- `report_consistent()` runs 6 deterministic checks; any failure = structural failure, report cannot be published
- `resolve_where_paths()` ensures all `where` paths in findings exist in the worktree
- 3-gate publish rule: structural=ok AND verdict∈{partial,fail} AND severity≥medium floor
- Retry on structural failure: MAX 2 attempts, FIX preamble naming the defect, second failure records and continues
- Empty findings with `verdict=pass` is a complete correct report — never penalize clean sessions
- L4 runs stored in the existing `runs` table with `kind='l4'`, `parent_run_id=<parent>`
- L4 runs excluded from active-run-per-project constraint (`idx_runs_active_project` filtered)
- Legacy `l4_standalone`, `l4_acceptance`, `l4_status`, `l4_reason` columns kept as deprecated
- `spec_hash` (16-char SHA-256 of goal+spec) stored on L4 run for scenario reuse tracking
- Migration `v7_040_l4_mvp2.sql` adds columns: `kind`, `parent_run_id`, `l4_scenarios`, `l4_report`, `l4_structural`, `spec_hash`
- Manual override via `POST /l4/manual` — accepts same `L4Report` model+checks, uses `labeled_by='human'`

**Rationale**: Structured L4Report with deterministic validation eliminates heuristic fragility. Pre-registered scenarios give the agent a clear success criterion before it sees the repo — "if you can't figure out HOW from RUN.md, that IS the finding." The 3-gate rule ensures findings are only surfaced when they meet quality and severity thresholds.

**Trade-offs**: Still uses AionUi ACP polling (transitional — future should use watcher-observed sessions). Scenarios are LLM-generated and may inherit LLM biases. The fixed severity floor (medium) may miss cumulative low-severity issues. `on_run_completed` still blocks the evaluator consumer during polling.

**Files**:
- `shared/l4_models.py` — Pydantic models, `report_consistent()`, `resolve_where_paths()`, `hash_spec()`
- `services/evaluator/l4_scenarios.py` — `generate_scenarios()`, scenario = (as_a, wants, success_looks_like)
- `services/evaluator/l4_brief_template.py` — Jinja2 brief, "empty findings is complete" verbatim
- `services/evaluator/l4_runner.py` — `run_l4_phase()` orchestration, `_validate_report()`, `_should_publish()`, `_emit_l4_findings()`
- `backend/migrations/v7_040_l4_mvp2.sql` — DB migration

## 2026-07-02 — L4 isolated execution workspace

**Status**: ACTIVE

**Context**: The first event-driven L4 implementation spawned the persona directly in the completed run worktree. In the whole-stack proof this allowed the L4 agent to inspect and attempt edits to product source, violating the L4 contract: persona uses the product as a black-box user and must not mutate the artifact under evaluation.

**Decision**:
- Evaluator-svc prepares a per-run isolated copy at `workspace/l4_runs/<run_id>/` before spawning L4.
- The copy is made from the run worktree and must contain `RUN.md`.
- Conductor parses deterministic install/setup commands from `RUN.md` and runs them before freezing the copy.
- Evaluator writes a local `opencode.json` in the copy that denies edits globally, allows edits only under `l4_scratch/**`, denies git/destructive/sudo commands, and denies webfetch/websearch.
- Evaluator freezes product source via chmod after dependency installation, then re-opens runtime dirs (`l4_scratch`, `.venv`, `node_modules`, caches/log dirs) for execution.
- AionUi ACP L4 conversations are spawned with `workspace=<isolated copy>`, not the original run worktree.
- L4 prompt is observational-only: read `RUN.md`, run the product, exercise the scenario, do not inspect/edit/fix source, write notes only to `l4_scratch/`.
- Source immutability is verified inside the isolated copy after L4. Mutation fails the run as `l4_status='run_failed'`.
- Current operational behavior keeps `workspace/l4_runs/<run_id>/l4_scratch/l4_report.md` residue after a real L4 run so humans can inspect the persona output. Product source remains isolated from the original run worktree.

**Rationale**: Full `cp -r` isolation plus chmod and local OpenCode permissions absorb L4 non-determinism while preserving the evaluated artifact. Keeping the scratch report is useful during P0/P1 debugging.

**Trade-offs**: Evaluator still polls AionUi synchronously for now, which is architecturally less clean than delegating completion detection to watcher. A future refinement should make L4 a first-class watched session/event rather than blocking the evaluator consumer.

## 2026-07-02 — Ratchet trigger event flow

**Status**: ACTIVE

**Context**: The ratchet (mine → mutate → validate → keep/revert cycle) had no event trigger. The `backend/evaluator/ratchet.py` module existed with full implementation but was never wired to an event consumer.

**Decision**:
- `ratchet.trigger` routing key added to evaluator queue's BINDINGS in `shared/bus.py`
- `on_ratchet_trigger()` handler in evaluator-svc calls `run_experiment(agent_config_id, node_type)`
- Pre-flight `assert_ready()` blocks if judge not trusted, heldout < 5, or no recent scores
- `run_experiment()` flow: mine failures → propose mutation → validate on heldout → record in `experiments` + `skill_mutations` tables
- Global-scope mutations (domain=backend/general) queued for human approval; project-scope auto-applied

**Rationale**: Event-driven ratchet enables isolated experiments without blocking the hot path. Pre-flight guard ensures experiments run only when calibration data is trustworthy.

**Trade-offs**: Ratchet is fully blocked until L3 calibration produces `trusted=true`. Real golden data is required — example-generated data produces random judge/human score alignment.

## 2026-07-02 — Golden set seeding (example-generated data)

**Status**: ACTIVE

**Context**: L3 calibration and ratchet validation require a frozen golden set of labeled artifacts, but no real human-labeled data exists. Development and testing need example data to prove the pipe flows.

**Decision**:
- `scripts/seed_golden_backend_api.py` inserts example-generated golden items for a node type
- Each row has `source='example-generated'` to distinguish from real human-labeled data
- Split into `calibration` (for L3 agreement computation) and `heldout` (for ratchet validation) — minimum 5 heldout required
- Scores are randomized (judge_score independent of human_score) — sufficient for pipe proof, not for real trust
- L3 calibration endpoint `POST /calibrate/{node_type}` in `backend/web/routes/calibrate.py` triggers scoring and writes `judge_trust`
- `seed_all.sh` orchestrates multi-agent-config seeding

**Rationale**: Generated data proves the pipe flows end-to-end without requiring human annotation. The `source` column enables filtering in production when real data arrives.

**Trade-offs**: Not real data — `MAE ≈ 0.5` and `agreement ≈ 0.15` typical. `judge_trust.trusted=false` until real calibrated data replaces it. Ratchet cannot run until judge is trusted.

## 2026-07-06 — Harness-agnostic agent profile import pipeline

**Status**: ACTIVE

**Context**: Ready-made agent profiles (agency-agents, wshobson) and skills (awesome-agent-skills) needed to be imported as Conductor staffable rows AND harness drop-in files without assuming a specific target harness format.

**Decision**:
- Import writes to a **neutral schema** (`agent_configs` with `source`, `import_ref`, `raw_definition`, `backend_targets`) — no harness-specific assumptions at import time
- A `HarnessRenderer` ABC in `backend/skills.py` converts neutral rows to harness-specific files (opencode now; any harness later)
- `RENDERERS` registry maps harness names to renderer instances; adding a harness = one new renderer implementation + registration, no re-import
- Skill layers: **global** (all skills, available to every run) and **worktree** (capability-scoped subset, installed pre-spawn per node)
- Capability→skill mapping in `capability_skills` table drives per-node worktree skill selection
- Collision guard: OMO reserved names + duplicate IDs get `imp-` prefix
- All imported agents default `backend_targets=["opencode"]`, `source="imported"`, model overridden to LiteLLM group

**Rationale**: Harness-agnostic import prevents lock-in — adding a new execution backend requires only a new renderer, not a re-import. Per-worktree skill scoping avoids context bloat from loading all 1177 catalog skills into every node.

## 2026-07-06 — JSONB family array for multi-domain capability matching

**Status**: ACTIVE

**Context**: Capabilities needed to belong to multiple families (e.g., `frontend` is both `software` and `design`) for domain-based pre-filtering in the capability selector. The original `family TEXT` column with equality matching (`WHERE family = %s`) could only represent a single family.

**Decision**:
- `capabilities.family` migrated from `TEXT` to `JSONB` array of strings
- Existing single-family rows wrapped in single-element JSONB arrays (e.g., `"software"` → `["software"]`)
- Selector pre-filter changed from `family = %s` to `family ?| %s::text[]` (JSONB any-string-overlap operator)
- GIN index (`idx_cap_family_gin`) replaces the old btree index for efficient `?|` queries
- `_FALLBACK_CAPS` in `registry.py` updated: all `family` values are now lists
- `DOMAIN_TO_FAMILY` in `selector.py` updated: values are `list[str]` instead of `str`; `design` domain maps to `["design", "creative"]` for backward compatibility
- `frontend` capability carries `["software", "design"]` — selectable by either domain
- `image_gen`, `design_layout` carry `["creative", "design"]`
- `tech_docs` carries `["software", "research"]`

**Rationale**: JSONB array with `?|` overlap query is the simplest Postgres-native approach — no join table needed, no schema complexity, existing GIN index support. A single capability can match multiple domain pre-filters without duplication.

**Trade-offs**: JSONB operators are slightly less readable than TEXT equality in queries. The `?|` operator requires a `text[]` argument — callers must pass a list, not a scalar. Older psycopg versions need explicit `::text[]` cast.

## 2026-07-06 — Heterogeneity stress test data pipeline (generated, provisional)

**Status**: ACTIVE

**Context**: Proving the moat machinery works across heterogeneous domains required realistic, multi-domain stress test data. Manual authoring of 90 goals across two domains (Software Delivery + Content Studio) was impractical. Goals needed to span verification-strength tiers (strong-oracle, mixed, weak-oracle, unrealizable).

**Decision**:
- `scripts/seed_stress_domains.py` seeds 12 capabilities (7 Software Delivery + 5 Content Studio) with JSONB family arrays and objective+subjective quality dimensions, plus 3 agent_configs (sw-fullstack-opencode, sw-backend-hermes, content-writer-opencode)
- `scripts/gen_stress_goals.py` generates 90 goals (45/domain: 15 small + 15 medium + 15 large) via free LiteLLM (`deepseek-planning`) with `source='generated'`
- Goals stored in `stress_goals` table with `domain`, `scope`, `title`, `spec`, `expected_capabilities`, `source='generated'`
- Content Studio caps (`image_gen`, `music_generation`) intentionally left partially unstaffed — triggers honest gap/realizability failures as expected
- The `gen_stress_goals.py` script reads `LITELLM_KEY_PLANNING` (fallback from `LITELLM_GATEWAY_KEY`) for LiteLLM auth
- `source='generated'` marks auto-generated goals; `source='labeled_by=gpt'` with `confidence='provisional'` marks auto-labeled golden

**Rationale**: Generated data proves pipe flow end-to-end across heterogeneous domains without human annotation. The scope tiers (small/medium/large) enable staged execution — run small first for quick validation, large overnight.

**Trade-offs**: Not real user goals — edge cases may differ from production. Large goals (5+ capabilities) are expensive to run end-to-end. The `expected_capabilities` field is LLM-generated and may hallucinate capabilities not in the registry.

## 2026-07-23 — L2 rubric artifact-answerability constraint + evaluator infra-file exclusion + dirty-state merge guard

**Status**: ACTIVE

**Context**: L2 rubric items generated by the meta-planner's `check_generator.py` and `checkgen.py` frequently asked about binary deliverables (`dist/app`), compiled outputs, or runtime behavior — artifacts that repomix (the L2 judge's input) cannot include because it excludes binary files and non-text outputs. This produced inevitable score-0 failures regardless of node quality. Separately, `AGENTS.md` consumed 64.8% of the repomix snapshot for a non-product file. Also, `finalize_success()` failed when the main repo's `master` branch had uncommitted changes from prior operations.

**Decision**:
- Added artifact-answerability constraint to both `check_generator.py` (CHECKGEN_PROMPT, line 200-207) and `checkgen.py` (L2_GEN_PROMPT, line 38-45): L2 rubric items must be answerable from visible artifacts (source files, config, docs, build scripts) and must never require inspecting non-text deliverables, compiled binaries, build outputs, generated assets, runtime behavior, or installed artifacts.
- Added `"AGENTS.md"` to `ARTIFACT_SKIP_PARTS` in `l2_judge.py` and applied `_artifact_skip_path()` filter to `changed_files` in `_build_repomix_snapshot()`.
- Added dirty-state stash guard in `_git_merge()` (`lifecycle.py`): before merging, check `git status --porcelain` on the main repo; if dirty, `git stash --include-untracked` to prevent merge failure.

**Rationale**: The artifact-answerability constraint eliminates impossible-to-pass L2 checks by preventing the LLM from writing rubric items about invisible outputs. The infra-file exclusion reduces token waste and prevents AGENTS.md from diluting the repomix signal. The dirty-state guard prevents merge failures from leftover changes in the main repo.

**Trade-offs**: The constraint may be overly conservative for domains where binary output is the primary deliverable (e.g., compiled firmware). L1 deterministic checks remain available for binary existence verification. The stash may hide legitimate uncommitted work — logs capture the stash event for debugging.

## 2026-07-22 — Plan-level L2 steering feedback fix (RAW ERRORS merge + deps_correct + measurable rubric + feedback text)

**Status**: ACTIVE

**Context**: The planner's revise loop produced contradictory feedback: `CORRECT` listed files with valid structure while `RAW ERRORS` said to fix them, because `render_deterministic_feedback()` only checks JSON structure, not GATE/policy outcomes. Additionally, the plan L2 judge flagged sequential dependencies as "spurious edges" since the judge prompt had no sequential constraint. Design/visual domains (`visual_design`, `design_layout`) had no matching rubric profile, causing L2 judge failures. Finally, `gate_plan()` emitted opaque `"[feedback degraded]"` strings instead of the LLM's actual `what`/`why`/`how` feedback, starving the meta-planner of actionable guidance.

**Decision**:
- `_extract_fix_files_from_raw_errors()` in `harness_worktree.py` parses `node-NNN:` patterns from staffing error lines via `re.finditer(r"(node-\d{3}):", line)`, mapping to `.plan/nodes/node-NNN.json` + `.plan/checks/node-NNN.json` paths.
- `retry_brief()` merges RAW ERROR file paths into the `fix_files` set, so `FIX THESE` includes scoped file references even when structure is clean but GATE fails.
- Instruction text updated: "Fix ONLY FIX THESE (includes files referenced in RAW ERRORS). Do NOT touch CORRECT unless also in RAW ERRORS."
- `PLAN_JUDGE_PROMPT` updated with SEQUENTIAL CONSTRAINT: "Nodes MUST be sequential (each depends on previous). Do NOT flag sequential dependencies as unnecessary."
- `deps_correct` rubric updated: "Are dependencies sequential and correctly ordered? Each node must depend on the previous (no parallel branches)."
- `measurable` rubric updated: "Does each node have a measurable success criterion appropriate to its domain? Code nodes need deterministic checks; design/visual nodes may use rubric-based quality checks."
- `gate_plan()` feedback string now includes `what`/`why` text instead of opaque `"[feedback degraded]"` — format: `"deps_correct: not met — dependencies include spurious edges [feedback degraded]"`. The degraded marker is an appendix, not a replacement.

**Rationale**: Fixing the feedback contradiction ensures the meta-planner receives reliable, actionable signals. Sequential constraint and domain-appropriate rubrics prevent false gate failures on valid linear DAGs and design deliverables. Preservation of `what`/`why` text ensures the meta-planner can act on gate feedback even when DimFeedback rejects the plan-level `where` format.

**Trade-offs**: The `_extract_fix_files_from_raw_errors` parsing is regex-based and depends on the `node-NNN: cap needs tools` error format — changes to staffing error messages may require regex updates. Sequential constraint trades potential parallelism for deterministic linear execution. Design domains remain harder to evaluate objectively.

## 2026-07-02 — Example-generated data marking convention

**Status**: ACTIVE

**Context**: Throughout development and testing, we create data (projects, plans, agent configs, golden set items) to prove pipe flows. Production systems must distinguish real data from example-generated test data.

**Decision**:
- All example-generated rows must set `source = 'example-generated'` where the column exists
- Tables with `source` column: `agent_configs`, `golden_set`, `experiments`, `skill_mutations`
- Real human-labeled data uses `source = 'human'`; auto-generated production data uses appropriate source tag
- No example-generated data survives to production; the pipe is proven and then the data is replaced

## 2026-07-27 — Intake-svc microservice (L4 findings → improvement goals)

**Status**: ACTIVE

**Context**: L4 persona simulation produced structured friction scores but had no downstream consumer — `l4.findings` events were emitted into the void. The evaluator needed an event-driven intake service to convert findings into improvement goals without blocking the hot path.

**Decision**:
- New microservice `intake-svc` on port `:8095` consumes `l4.findings`, `run.failed`, `plan.awaiting_clarification`, `plan.ratifiable`, `plan.failed`, and `plan.rejected` events from `intake.q`
- `Intake-svc` runs as `uv run uvicorn services.intake.main:app --port 8095` with its own `.env` at `services/intake/.env`
- Intake stores findings in the `intake_findings` table with `source`, `finding_type`, `payload`, `project_id`, and `status='open'`
- Intake exposes `GET /api/intake/findings` to list open findings and `POST /api/intake/findings/{id}/ack` to acknowledge/close
- On `l4.findings` events, intake creates one finding row per entry in the findings list with `finding_type='l4_finding'`
- On `run.failed` events, intake creates a finding with `finding_type='run_failure'`
- Intake runs in the same RabbitMQ topology with durable queue `intake.q` bound to `conductor.events`
- Intake uses the same shared outbox + deduplication pattern as other services
- Intake test suite at `services/intake/tests/`

**Rationale**: A dedicated intake service decouples findings ingestion from the evaluator hot path and provides a REST API for humans and tools to query open findings. Follows the same microservice pattern as planner/executor/watcher/evaluator.

**Trade-offs**: Intake is read-heavy for queries but write-light for ingestion — a simple FastAPI service is sufficient. The intake_findings table is append-only until ack'd; no archival story yet.

## 2026-07-28 — L4 watcher-observed completion (removed blocking AionUi polling)

**Status**: ACTIVE

**Context**: The original L4 MVP2 ran as a blocking synchronous poll inside `on_run_completed()` — the evaluator consumer was blocked for 10+ minutes per L4 cycle while polling AionUi ACP conversations. This was explicitly marked as "transitional" in the MVP2 decision. L4 also used ad-hoc polling (`_poll_l4_session()`) instead of the existing watcher-observed node_session pattern that meta-planner and executor nodes use.

**Decision**:
- Rewrote l4_runner.py: removed `_poll_l4_session()`, `_validate_and_publish()`, `MAX_L4_ATTEMPTS`, `L4_CASE_TIMEOUT_S`
- `run_l4_phase()` is now spawn-only: generate scenarios → create L4 run → create node_session (`role='l4'`, `backend='opencode'`) → write scenarios → spawn AionUi → update conv_id → emit NodeSpawned → RETURN
- NodeSpawned event is consumed by watcher-svc, which polls the L4 worktree like any other node
- Watcher-svc has L4-specific settle config: `_SETTLE_S_L4` (60s) and `_STABLE_POLLS_L4` (5 polls) via env vars, registered in `_ROLE_CONFIG`
- `on_node_observed()` in evaluator-svc routes `role='l4'` to `_on_l4_observed()` which validates the L4Report, runs `report_consistent()`, and emits `l4.findings`
- Publish gate loosened: emit findings whenever report is parsed (even if structural validation fails). Only JSON parse failure blocks emit.
- `_prepare_l4_workspace()` does `git init` + initial commit so watcher's `_git_state_signature()` can track file changes
- Workspace persists until `_on_l4_observed()` cleans up — do NOT clean from `on_run_completed()` if NodeSpawned was emitted
- `Run` SQLAlchemy model fixed: added missing `project_id = Column(String, nullable=False)` to `shared/models.py`

**Rationale**: Reuses the same runs + node_sessions + watcher infrastructure as meta-planner and executor nodes — no ad-hoc polling, no blocking evaluator consumer. The watcher already knows how to detect terminal states. Loosened emit gate ensures structural validation failures still surface findings for human review rather than silently dropping them.

**Trade-offs**: L4 now depends on watcher-svc being operational. L4-specific settle config (60s/5 polls) is longer than execution nodes (30s/2 polls) — L4 agents need more time to complete scenarios. The loosened emit gate may produce findings with inconsistent structure; downstream consumers must handle partial data gracefully.

## 2026-07-28 — Intake L4 severity rank alignment

**Status**: ACTIVE

**Context**: The L4 report model defines severity as `Literal["low", "medium", "high"]`, but the `L4FindingsAdapter._SEVERITY_RANK` in intake used `{"fatal": 4, "critical": 3, "error": 2, "warning": 1}` with `_MIN_SEVERITY=2`. Since `"high"` was not in the rank dict, all findings mapped to rank 0 and were silently filtered out — no improvement intents were created from L4 findings.

**Decision**:
- Changed `_SEVERITY_RANK` in `services/intake/adapters/l4_findings.py` from `{fatal:4, critical:3, error:2, warning:1}` to `{high:4, medium:2, low:1}`
- `_MIN_SEVERITY` stays at 2 — meaning `high` and `medium` findings are accepted, `low` findings are dropped
- This matches the `report_consistent()` check #6 which also treats `"high"` as the threshold for high-severity

**Rationale**: Severity labels must match between L4's output model and intake's severity filter. The old rank dict was a copy-paste from a different domain (error monitoring). Using L4's actual severity values is the correct fix.

**Trade-offs**: No behavioral change for the severity floor — `medium` cutoff was always the intent, it just wasn't working because `"high"` was unrecognized. Low-severity findings are still dropped but this is by design.

## 2026-07-31 — L4 execution model: agent-driven (guide 05, Option B)

**Status**: ACTIVE

**Context**: The L4 runtime design guide (05_l4_runtime.md) specifies a deterministic driver model: per-component `run_block` execution, an `L4_DRIVERS` map keyed by delivery form, `await_health`, env tiers, and port allocation. The current implementation instead spawns an LLM persona agent into an isolated workspace — the agent reads RUN.md, runs the product black-box, and writes an `L4Report`. The File 05 todos required either adopting the full driver model (large effort, removes the LLM from the L4 loop) or formally adopting the agent-driven model with hygiene fixes.

**Decision**:
- Adopt the agent-driven model as the chosen design (Option B). The deterministic driver model from guide 05.1/05.2 (run_block, L4_DRIVERS, await_health, env tiers, port allocation) is NOT implemented — documented deviation, gate 05 status = documented-deviation.
- Install/setup commands are sourced from the project manifest `.conductor/workspace.json` (`components[].commands.setup`), NOT parsed from RUN.md. This supersedes the 2026-07-02 bullet "Conductor parses deterministic install/setup commands from RUN.md".
- Each component's setup runs in its own `subdir` inside the isolated copy, in one shell with `set -e` (guide 05.9 semantics) — no `cd`-line parsing.
- Assembly/system projects (root `workspace.json` with `services`) have no local setup — install is skipped (services run under docker-compose).
- Source immutability is enforced AND verified: `_prepare_l4_workspace()` persists a SHA-256 source signature to `l4_scratch/source_baseline.json`; `_on_l4_observed()` verifies the copy is unchanged before publishing findings. A mutated source fails the run as `l4_status='run_failed'` and emits no findings.

**Rationale**: Keeps the LLM in the L4 loop (agent discovers HOW from RUN.md and drives the product like a real user — closer to persona simulation than scripted checks) while eliminating the RUN.md-parsing fragility: commands now come from the authoritative manifest written by the planner, and immutability is verified at completion rather than assumed.

**Trade-offs**: The 9 delivery-form drivers from the guide remain unimplemented — products that cannot be driven interactively depend on the agent's tooling. Install is skipped entirely for legacy projects without a manifest; they get no dependency setup.

## 2026-08-01 — Worksystem: publish-on-merge derived store + snapshot system L4

**Status**: ACTIVE

**Context**: The design guide (10_worksystem.md) specifies a worksystem — a per-system store of every member's *published* state that makes multi-component systems composable and L4-testable from artifacts alone. Before File 10, merged runs committed their artifacts only to the master worktree; there was no system-level index, no compose, and no way to run a system L4 against real published state.

**Decision**:
- Add a `backend/worksystem/` package: `repo.py` (per-system git repos under `worksystem/repos/<system_id>/`), `index.py` (`index.json` regenerated on every publish, never patched), `compose.py` (compose.yml + .env.example rendered from index), `publish.py` (publish-on-merge), `snapshot.py` (git worktree snapshot sandbox), `adjustments.py` (compose-diff → recurring-adjustment finding).
- **Publish-on-merge**: on `run.merged`, the executor calls `publish_run()` — the member's committed artifacts (per the standard's `publish_manifest.files`/`artifacts`) are copied into `members/<name>/` with `_source.json` provenance, then `index.json` + `compose.yml` are regenerated and committed. A failed publish marks the run `publish_status='stale'` and **never fails the run**. Skipped for system-less projects and assembly composers.
- **Sandbox-by-absence**: system L4 runs off a git worktree snapshot (`snapshot_worktree()`, detached HEAD, tag `l4/run-<run_id>`, retain policy `L4_TAGS_RETAIN`) — "artifacts only, no source" (guide 10.5). `_write_worksystem_opencode_json()` allows edits (an edit to `compose.yml` is the adjustment signal) but denies `git *`/`sudo *`/`rm -rf *`/webfetch/websearch.
- **Adjustment-delta**: `compute_adjustments()` diffs compose between runs; `same_adjustment_in_last_n_runs()` (window `RECURRENCE_WINDOW`=3) escalates a recurrence finding even on pass verdicts; `tag_possibly_stale()` drops findings about members whose published state already lags master (intake filters them).
- Worksystem repos are **derived state** — gitignored (`worksystem/`), never committed to the Conductor repo.

**Rationale**: Keeps the worksystem a regenerable view of merged artifacts rather than a second source of truth. Publish failure must never alter a run's outcome. The snapshot-without-source sandbox gives the guide's isolation while letting the compose file be the agent's adjustment surface.

**Trade-offs**: Worksystem content is lost on a fresh checkout (regenerated on next publish). Assembly composers remain on the legacy generator path and are excluded from publish. The compose-diff signal requires a `docker compose config` parse to normalize semantic deltas.

## 2026-08-05 — References store: per-project read-only context in worktrees

**Status**: ACTIVE

**Context**: Some projects need immutable reference material (docs, conventions, third-party docs) visible to both the meta-planner and execution agents. Reusing the `deps/` mechanism was wrong (deps are system-goal driven and request-scoped). The store-first design avoids any request-schema change and reuses the existing "structured file = valid entity" convention (README.md as validity gate, like SKILL.md / WORKSPACE.md).

**Decision**:
- **Store-first**: reference repositories live under `references/<project_id>/` (root `REFERENCES_ROOT`, default `/opt/aipc/conductor/references`) with a `README.md` as the validity/context-summary gate. No `GoalRequest` / request-field changes.
- **Auto-detected by project_id**: `has_references(project_id)` returns True only when the project dir exists AND contains a `README.md`. A missing/invalid store is a silent no-op — no errors, no permission changes.
- **Copied into worktrees**: `copy_references(project_id, worktree)` copies the store to `.conductor/references/` in both planning worktrees (`create_planning_worktree()`) and execution worktrees (`assemble_for_spawn()`). `.git` is never copied — context, not git history.
- **Gitignored, never committed**: `.conductor/references/` is appended to the worktree `.gitignore` (`gitignore_references()`). `.conductor/` is already in `INFRA_EXCLUDES` so references never reach watcher signatures, evaluator artifacts, or commits.
- **Read-only**: when references exist, the worktree permission file denies edits under `.conductor/references/**` (planning opencode.json always; execution `_deny_references_edit()` conditional on `has_references`). Agents read, never modify.
- **Brief pointer**: `planning_brief()` appends a `REFERENCES (read-only context — do not edit):` block listing each `README.md` relative path when the store was copied.

**Rationale**: Immutable reference context should be available to both planning and execution without schema changes, be excluded from evaluator/git surfaces automatically via existing `.conductor/` exclusions, and degrade gracefully when absent.

**Trade-offs**: References are derived state — a fresh checkout loses them until re-seeded (gitignore-only, never committed). The README gate means a store without a README is invisible. Execution deny-write is conditional on `has_references`, so adding a store later changes permissions for existing projects.
