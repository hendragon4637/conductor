# Conductor — assembled context (do not edit; regenerate via .memory/assemble.sh)

## Locked decisions (ACTIVE only)
## 2026-06-30 — Microservice event-driven architecture (RabbitMQ + transactional outbox)
## 2026-06-30 — Microservice ports and service boundaries

## Glossary
# Project Glossary

| Term | Definition |
|---|---|
| **Conductor** | The control-plane orchestrator. FastAPI backend + watcher + DB. Manages sessions, plans, projects. |
| **AionUi** | The agent orchestration server (OpenCode-based). Receives tasks from Conductor, spawns agents, reports back. |
| **Node** | A single step in a plan. Has kind (task/review/approval), members, worktree. |
| **Worktree** | Git worktree per node. Isolated working directory where the agent operates. |
| **Watcher** | Service that polls git diff + cheap query per node, detects when work is "done" (stable for N polls). |
| **Ratchet** | The one-way progress model: a completed node cannot be re-opened; the plan advances forward only. |
| **Verdict** | The deterministic terminal decision produced by the watcher from polling signals. |
| **Session** | A conversation with an agent. Spans one poll cycle. Contains messages, decisions, context. |
| **Plan** | A directed graph of nodes. Can be draft (in-memory) or promoted (DB-persisted). |
| **Meta memory** | Dev memory for Conductor itself. Files + Git + Repomix. Survives compaction. |
| **Product memory** | Per-project temporal knowledge graph. Graphiti (Neo4j). Assembled into node worktrees at spawn. |
| **Group ID** | Graphiti partition key encoding scope hierarchy: `namespace:project:agent:session`. |
| **Gate** | A pass/fail check at the end of a build file. Must pass before proceeding to next file. |
| **Repomix** | Tool that packs a repo into a single compact Markdown file (repo structure snapshot). |
| **LLM extraction** | Batch LLM call per session to extract atomic memory facts (decisions, conventions, error patterns). |
| **Promotion (gated)** | Elevating a memory from session scope to project or global scope, requires human approval. |
| **Settle time** | Minimum quiet period (seconds) before watcher marks a node terminal. |
| **Signal** | A data point the watcher polls: git state signature or cheap DB query signature. |
| **Meta-Evaluator** | Quality gate system between watcher "done" verdict and node commit. Four layers: L1 deterministic, L2 rubric judge, L3 jury meta-eval, L4 persona simulation. L4 runs out-of-band via watcher-observed node sessions after the run completes. |
| **Check** | A single evaluation criterion (deterministic shell command or rubric yes/no question). Generated at decompose, ratified at plan approval. |
| **Remediation node** | A bounded retry node appended to the plan DAG when evaluator gates fail. Same checks, same members, capped attempts. |
| **Ratchet** | One-way progress model: completed nodes cannot be re-opened. Quality scores from the evaluator inform ratchet decisions. |
| **L1 (deterministic gate)** | First evaluator layer: runs shell commands in the worktree (pytest, curl, py_compile). Cheap, no LLM. |
| **L2 (rubric judge)** | Second evaluator layer: LLM judge scores node output against preset rubric items. Fires only if L1 passes. |
| **Rubric preset** | Pre-authored set of rubric questions for a node type (build, test, review, design, default). Applied at decompose, not generated zero-shot. |
| **Artifact** | Evidence collected from the worktree (git diff, new files, test output) shown to the L2 judge for rubric evaluation. |
| **L2 (rubric judge)** | Second evaluator layer: LLM judge scores node output against preset rubric items per item, returns weighted score. Fires only if L1 passes. |
| **Held-out set** | A subset of golden tasks NOT used for mining failures — used to validate that a mutation generalises and does not overfit to seen failures. |
| **Mining set** | A subset of golden tasks (or historical Langfuse traces) examined for recurring rubric-level failure patterns in `mine_failures()`. |
| **Scope gating** | Policy: global-scope agent configs (backend/general domain) queue winning mutations for human approval; project-scope configs may auto-apply. |
| **Frozen boundary** | Artifacts the ratchet may NOT mutate: permissions, engine, model, golden set, budget caps, check_cmd. Only probabilistic artifacts (skill, agents_md, prompt, rubric, judge-prompt) are mutable. |
| **Self-Harness** | Discipline for ratchet experiments: mine recurring failures, propose minimal targeted edits to probabilistic config only, validate on held-out set with no regression, keep model + golden anchor frozen. |
| **L3 (meta-evaluation)** | Third evaluator layer: a diverse-family jury periodically checks the L2 judge's verdicts against a frozen human golden set. Drift triggers a gated rubric-refinement proposal (never auto-applied). |
| **Jury (diverse panel)** | A panel of ≥2 different model families that independently score artifacts. Reduces correlated bias in L2 judge calibration. |
| **Golden set** | A frozen, human-curated set of labeled (input, artifact, expected_score/criteria_met) examples per node-type. Written ONLY by human action — the anchor that prevents evaluator drift. |
| **Rubric refinement** | A proposed edit to the L2 judge's rubric wording, triggered when L3 detects drift beyond tolerance. Always queued for human approval, never auto-applied. |
| **L4 (persona simulation)** | Fourth evaluator layer: runs out-of-band after the parent run completes. Generates intent-level scenarios from goal+spec pre-session, creates a watcher-observed node_session (`role='l4'`), spawns an agent to attempt each scenario black-box, validates the structured L4Report on completion, and emits `l4.findings`. Uses the same watcher-observed pattern as execution nodes instead of blocking AionUi polling. |
| **L4Scenario** | An intent-level scenario generated from goal+spec BEFORE the agent sees the repo. No steps — the agent must figure out HOW from RUN.md. Three fields: `as_a` (user role), `wants` (goal), `success_looks_like` (expected outcome). Source is `seeded` (pre-generated) or `adhoc` (agent-invented, max 2). |
| **L4Report** | Structured output from an L4 session (Pydantic model in `shared/l4_models.py`). Fields: `verdict` (pass/partial/fail), `scenario_results`, `findings` (things that should change), `observations` (praise, never routed). Validated via `report_consistent()` with 6 deterministic checks. |
| **report_consistent** | Six deterministic consistency checks on an L4Report: (1) every seeded scenario has a result, (2) adhoc count ≤ 2, (3) every finding references a known scenario_id, (4) failed/blocked scenarios have at least one finding, (5) verdict=pass has empty findings, (6) verdict=partial has no high-severity findings and negative verdicts have findings. Any failure = structural failure, report cannot be published. |
| **scenario_id** | Identifier linking each finding to the scenario attempt that produced it. Must reference a known `scenario_id` from `scenario_results`. Stable across L4 retries per project for future reuse/graduation. |
| **spec_hash** | Deterministic 16-char SHA-256 prefix hash of `goal+spec`. Stored on the L4 run for scenario reuse tracking. Computed via `hash_spec()` in `shared/l4_models.py`. |
| **3-gate publish rule** | L4 findings are only emitted when ALL three gates pass: (1) structural validation = `ok`, (2) verdict ∈ `{partial, fail}`, (3) at least one finding has severity ≥ floor (default: medium). Below-floor findings are retained in JSONB but not sent. |
| **MCP (Model Context Protocol)** | Protocol for exposing tools and resources to LLM applications. Conductor and Obsidian vault are MCP servers over SSE transport. |
| **SSE transport** | Server-Sent Events transport for MCP — used when client and server are on different machines (human PC → AIPC). |
| **Conductor MCP** | MCP server on `127.0.0.1:8092` exposing safe plan operations (`conductor-create_plan`, `conductor-refine_plan`, `conductor-get_plan`, `conductor-list_sessions`, `conductor-search_memory`). No approve/spawn. |
| **Obsidian vault MCP** | MCP server on `127.0.0.1:8093` exposing `/home/aipc/conductor-notes/` as read-only markdown resources via `obsidian-read_note`. |
| **HarnessRenderer** | Abstract base class in `backend/skills.py` for converting neutral agent/skill DB rows to harness-specific files. One subclass per execution backend (opencode, stub, future). |
| **Global skills** | Skills installed to `~/.config/opencode/skills/` — available to every run regardless of node capabilities. |
| **Worktree skills** | Skills scoped to a node's capabilities, installed into `.opencode/skills/` in the worktree pre-spawn via `install_worktree_skills()`. |
| **capability_skills** | DB table mapping capabilities to skill IDs — drives per-node worktree skill selection. |
| **Import pipeline** | The automated process of cloning external repos (agency-agents, wshobson, awesome-agent-skills), parsing to neutral rows, classifying capabilities, and inserting to DB with `source='imported'`. |
| **Hermes Agent** | Nous Research v0.16.0 — second execution backend alongside AionUi. Self-routing agent core; receives one goal per node from Conductor, self-decomposes, and routes to its own subagents. Runs via HTTP API (`:8642/v1`), Docker sandboxed, with Conductor worktree mounted at `/workspace`. |
| **Calibration** | The L3 process of re-scoring frozen golden artifacts via the L2 judge and computing MAE and agreement. Results are stored in `judge_trust`. Does NOT modify the golden set. |
| **CalibrationReport** | Output from `calibrate()`: node_type, total golden items, agreement rate, MAE, trusted boolean, per-item `CalibrationItem` list, and a human-readable note. |
| **CalibrationItem** | A single golden item's calibration result: item_id, judge_score, human_score, judge_met, human_met, absolute_error. |
| **Judge trust** | A score/fact in the `judge_trust` table recording how well the L2 judge's scores agree with the human golden set for a given node_type. Trusted when MAE ≤ 0.15 and agreement ≥ 0.80. |
| **Plan evaluator** | A pre-execution gate that checks plan DAG structure (L1: nodes valid, fields present, deps resolve, acyclic) and optionally applies plan-structure rubrics (L2) at ratification time. Produces `plan_goal_review` stored on the run. |
| **PlanL1Result** | Output from `run_plan_l1()`: passed boolean, checks list (per-check passed/detail), and a human-readable note. Checks cover: at least one node, per-node required fields, dependency resolution, and acyclicity. |
| **PlanEvalResult** | Output from `evaluate_plan()`: L1 result, optional L2 result, `plan_goal_review` score, combined passed verdict, and note. Falls back to L1-only if L2 judge is unavailable. |
| **Plan goal review** | A score (0.0-1.0) stored on the run indicating plan quality. Set by `evaluate_plan()` at ratification time. Gates plan approval, not node execution. |
| **ExperimentResult** | Output from `run_experiment()`: agent_config_id, node_type, mutation applied (bool), validated without regression (bool), scope (project/global), winner text, experiment_id, mutation_id, heldout results. |
| **Mutation** | A candidate agent config edit produced by `propose_mutation()`. Contains the target field, old text, new text, and a rationale string summarizing the failure cluster. |
| **Pattern** | A mined failure cluster from `mine_failures()`: rubric_item, fail_count, example artifacts (list), and a synthesized pattern description. Input to `propose_mutation()`. |
| **EventBus** | RabbitMQ topic exchange (`conductor.events`) with per-service durable queues (`planner.q`, `executor.q`, `watcher.q`, `evaluator.q`, `intake.q`). Each queue binds to routing keys matching the events its service consumes. |
| **Transactional outbox** | Reliability pattern: events are written to an `outbox` table atomically with the business DB transaction. A background relay thread (`relay_loop()`) publishes pending outbox rows to RabbitMQ and sets `published_at`. Services deduplicate via `processed_events` on consume. |
| **Planner-svc** | Microservice on `:8093` — accepts `POST /goal`, `POST /clarify/{id}`, `POST /ratify/{id}`. Runs the planner graph (`formulate → inject → decompose → select_capabilities → generate_checks → gate`). Emits `plan.ratified`. |
| **Executor-svc** | Microservice on `:8091` — consumes `plan.ratified`, calls monolith's `launch_run()` to create worktrees and spawn AionUi teams. Emits `node.spawned`. Consumes `gate.evaluated` (finalize or advance DAG) and `node.remediate` (fix-forward retry). |
| **Watcher-svc** | Microservice on `:8092` — consumes `node.spawned`, polls worktrees via `_watch_loop()` (30s interval, 30s settle, 2 stable poll cycles). Sets `verdict=done_no_change` or `failed`. Emits `node.observed`. |
| **Evaluator-svc** | Microservice on `:8094` — consumes `node.observed`, runs L1 deterministic checks then L2 rubric judge. Emits `gate.evaluated` with outcome `done`/`remediate`/`failed`. Optionally emits `node.remediate` for retry. |
| **Intake-svc** | Microservice on `:8095` — consumes `l4.findings`, `run.failed`, `plan.awaiting_clarification`, `plan.ratifiable`, `plan.failed`, `plan.rejected`. Converts events into improvement intents stored in `intake_intents` table with `origin` set to the event source. Exposes REST API for listing and acknowledging findings. |
| **ServiceConfig** | Pydantic model (`shared.config`) loaded from environment per microservice. Fields: `service`, `env`, `rabbit_url`, `database_url`. Each service's `.env` overrides defaults. |
| **Outbox relay** | Background daemon thread per service that polls the `outbox` table for unpublished rows and publishes them to RabbitMQ. Uses its OWN pika connection — sharing the consumer channel corrupts the AMQP frame stream. |
| **Planner graph** | The LangGraph-based planning flow: `formulate → inject → decompose → select_capabilities → generate_checks → gate`. Defined in `services/planner/graph.py`. The `formulate` node converts raw goal to MetaGoal; `inject` enriches with domain conventions; `decompose` produces a DAG of nodes; `select_capabilities` assigns agent_configs per node via the capability selector; `generate_checks` creates L1/L2 checks per node; `gate` runs `run_plan_gate()` for L1+L2 plan evaluation. |
| **Conditional entry point** | `_route_entry()` in the planner graph that checks `state["status"]` to route to `"formulate"` (new plan, `status=="new"`) or `"inject"` (formulated/clarified plan, `status=="formulated"`). Replaces `set_entry_point("formulate")` to enable the clarify→continue flow. |
| **Clarify → continue flow** | The `/clarify` endpoint in `services/planner/main.py` re-invokes the planner LangGraph after `formulate_or_clarify()` returns a MetaGoal, enabling formulated plans to proceed through `inject → decompose → select_capabilities → generate_checks → gate` without manual re-submission. Previously, `/clarify` returned `formulated` status but never continued the graph. |
| **Revise loop (planner remediation)** | When the planner gate fails, `_n_decompose()` passes `gate_feedback` and prior `dag` from `PlanState` to `decompose()`, which injects them into the LLM prompt as a `{revision_block}` with fix-forward instructions. The LLM keeps working nodes and only fixes failures, rather than regenerating the entire DAG from scratch. |
| **Heterogeneity stress test** | A generated-data test proving the moat machinery works across multiple domains (Software Delivery + Content Studio) with varying verification strength. Covers: JSONB family-array selector, both backends (opencode + Hermes), L1/L2/L3/L4 evaluator layers, and the ratchet. |
| **Family array** | The `capabilities.family` column as a JSONB array of strings (e.g., `["software", "design"]`). Enables multi-domain capability matching via the `?|` overlap operator. Replaced the original single-string TEXT column. |
| **Stress goals** | Generated goals in the `stress_goals` table with `domain`, `scope` (small/medium/large), `title`, `spec`, and `expected_capabilities` — used for heterogeneity stress test execution. 90 goals total (45 domain × 15 scope). |
| **Provisional label** | A golden set label authored by a STRONGER model (ChatGPT Plus) rather than a human. Marked `labeled_by=chatgpt-plus`, `confidence=provisional`. Better than P0 (labeler ≠ judge breaks circularity), not as good as P3 (human = real ground truth). Swappable to human with zero system change (same `add_golden`, different `labeled_by`). |
| **Verification tier** | The strength of objective evaluation a capability supports: **strong-oracle** (backend_api, tests_suite — L1 deterministically verifiable), **mixed** (frontend, design_layout — L1 builds + L2 subjective), **weak-oracle** (copywriting, content_review — L1 file-exists only, L2 dominates), **unrealizable** (image_gen, music_generation — unsupported tools, honest skip). |
| **Skills store** | Per-skill directory under `skills_store/<skill_id>/` containing a `SKILL.md` and optionally a `scripts/` subdirectory. Each skill is fetched individually from its source GitHub repo, written to disk, then upserted to the DB with `store_path` pointing to its folder. |
| **Import pipeline** | The automated process of cloning external repos (agency-agents, wshobson, awesome-agent-skills), parsing to neutral rows, classifying capabilities, and inserting to DB with `source='imported'`. Skills are fetched sequentially (2s gap between requests) in interleaved batches of 30: fetch → LLM classify → upsert → next batch. |
| **Harness renderer** | A `HarnessRenderer` subclass in `backend/skills.py` that converts neutral DB rows to harness-specific config files. The opencode renderer copies per-skill folders from `skills_store/` to `~/.config/opencode/skills/` (global) or `.opencode/skills/` (worktree). |
| **NODE_BRIEF.md** | Static reference brief file written to the worktree by `_write_planner_opencode_json()`. Contains role, steps, rules, schemas, roster — the durable material that does NOT change between planning retries. |
| **plan_l2_raw_response** | `TEXT` column on the `plan_l2_judgments` table storing the raw (pre-Pydantic-parse) LLM response from the L2 plan judge. Observability-only; never fed into retry briefs. |
| **JUDGE_MODEL** | The model group identifier used for L2 plan evaluation, distinct from the meta-planner generation model (`deepseek-planning`). Set via `role="l2_judge"` in `call_llm_structured()`. |
| **RAW ERRORS merge** | `_extract_fix_files_from_raw_errors()` in `harness_worktree.py` parses `node-NNN:` references from staffing error lines and merges the corresponding `.plan/nodes/node-NNN.json` and `.plan/checks/node-NNN.json` paths into the `FIX THESE` file set, so the meta-planner sees scoped file references even when file structure is valid but GATE policy fails. |
| **Sequential constraint** | The `PLAN_JUDGE_PROMPT` requirement that nodes MUST be in sequential order (each depends on the previous). Added to prevent the L2 judge from flagging linear dependencies as "spurious edges." Parallel DAGs are not allowed. |
| **Feedback text preservation** | `gate_plan()` now includes the LLM's `what`/`why`/`how` feedback text in the gate feedback string, appending `[feedback degraded]` as a qualifier rather than replacing the content. Ensures the meta-planner receives actionable problem descriptions. |
| **Artifact-answerability constraint** | Prompt-level rule in `check_generator.py` and `checkgen.py` that prohibits L2 rubric items from requiring inspection of invisible artifacts (binaries, compiled outputs, build assets, runtime behavior, installed artifacts). Ensures every rubric item is answerable from the repomix text snapshot. |
| **ARTIFACT_SKIP_PARTS** | Set of file patterns in `l2_judge.py` that are excluded from the repomix snapshot sent to the L2 judge. Includes `.git`, `AGENTS.md`, and `opencode.json` — infra files that waste tokens and dilute the product signal. |
| **Worksystem** | Per-system store (File 10, guide 10.x) of every member's *published* state under `worksystem/repos/<system_id>/` — `members/<name>/` (workspace.json + _source.json provenance), regenerated `index.json`, and `compose.yml`. Derived state, gitignored, rebuilt on each publish. |
| **Publish-on-merge** | On `run.merged`, the executor calls `publish_run()`: the standard's `publish_manifest.files`/`artifacts` are copied into the member dir (with `.conductor/workspace.json` flattened to `workspace.json`), `index.json` + `compose.yml` regenerated (never patched) and committed. Failure marks `publish_status='stale'` and never fails the run; skipped for system-less projects and assembly composers. |
| **Worksystem snapshot** | Git worktree snapshot (`snapshot_worktree()`, detached HEAD, tag `l4/run-<run_id>`) of a worksystem repo used for system L4 — "artifacts only, no source" sandbox (guide 10.5). `_write_worksystem_opencode_json()` allows edits (compose.yml is the adjustment signal) but denies git/sudo/rm-rf/webfetch/websearch. |
| **Adjustment-delta** | `compute_adjustments()` git-diffs compose between runs → semantic delta → recurring-adjustment finding when `same_adjustment_in_last_n_runs()` (window `RECURRENCE_WINDOW`=3) matches, escalated even on pass verdicts. |
| **Possibly-stale finding** | `tag_possibly_stale()` tags findings naming members whose published state (per `_source.json` sha) lags master — intake drops them so fixes already on master aren't re-filed. |
| **Partial-scope run** | An L4 system run with an explicit `members` debug subset: `runs.partial_scope=true`, never blocked by missing members, publishes nothing. |

## Conventions
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

## Key commands
# Key Commands (Conductor development)

## Backend
```bash
cd /opt/aipc/conductor && uv run uvicorn backend.main:app --host 127.0.0.1 --port 8090
```

Notes:
- The FastAPI backend listens on `127.0.0.1:8090`
- The watcher starts inside `backend.main:app` on startup; do not start a separate `run_watcher.py` process

## Restart (from load-secrets + .env pattern)
```bash
# Free port 8090
fuser -k 8090/tcp 2>/dev/null || true
sleep 1

# Load secrets and env
set -a
source /opt/aipc/scripts/load-secrets.sh
source /opt/aipc/conductor/.env
set +a

# Restart backend (FastAPI on :8090)
cd /opt/aipc/conductor
setsid uv run uvicorn backend.main:app \
  --host 127.0.0.1 --port 8090 \
  > /tmp/conductor-backend.log 2>&1 &
echo "Backend started on :8090 (PID $!)"
```

## Testing
```bash
cd /opt/aipc/conductor && python -m pytest backend/tests/ -v              # all tests
cd /opt/aipc/conductor && python -m pytest backend/tests/test_gate22_watcher_query.py -v   # watcher query tests
```

## Database
```bash
docker exec postgres psql -U aipc -d aipc_conductor          # direct psql into Conductor app DB
docker exec postgres psql -U aipc -d aipc_conductor -c "\dt"  # list tables
docker exec postgres psql -U aipc -d aipc_conductor -c "<query>"  # one-shot query
```

Notes:
- Compose starts the Postgres service; the Conductor application database is `aipc_conductor` per `DATABASE_URL`
- Treat `DATABASE_URL` as the app-side source of truth for database name/host/credentials

## Memory
```bash
/opt/aipc/conductor/.memory/assemble.sh          # rebuild AGENTS.md from durable sources
/opt/aipc/conductor/.memory/repomix_refresh.sh    # refresh repo structure snapshot
/opt/aipc/conductor/.memory/obsidian_repomix_export.sh   # export chunked repomix notes into Obsidian
```

## Neo4j (product memory)
```bash
docker exec neo4j-aipc cypher-shell -u neo4j -p <pass> "MATCH (n) RETURN n LIMIT 10"  # query graph
```

## Evaluator (meta-evaluator gates)
```bash
cd /opt/aipc/conductor && python -m pytest backend/tests/test_evaluator_schema.py -v   # File 01: schema + check generation
cd /opt/aipc/conductor && python -m pytest backend/tests/test_evaluator_l1.py -v       # File 02: L1 deterministic gate
cd /opt/aipc/conductor && python -m pytest backend/tests/test_evaluator_l2.py -v       # File 03: L2 rubric judge
cd /opt/aipc/conductor && python -m pytest backend/tests/test_evaluator_e2e.py -v      # File 07: E2E test scenario
cd /opt/aipc/conductor && python -m pytest backend/tests/test_ratchet_wiring.py -v     # File 04: ratchet wiring (mining, validation, scope gating)
```

## Migrations
Database migrations are in `/opt/aipc/conductor/backend/migrations/`. New migration files prefixed with incrementing number + description (e.g., `011_fix_verdict_defaults.sql`). Run manually via psql with:
```bash
docker exec -i postgres psql -U aipc -d aipc_conductor < backend/migrations/<filename>.sql
```

## E2E test (monolith)
```bash
cd /opt/aipc/conductor && uv run python scripts/e2e_l2_test.py
```
Cleans state: `bash /opt/aipc/conductor/scripts/clean_e2e_state.sh`

## Microservices (event-driven architecture)

### Restart all 5 microservices
```bash
# Load secrets once
set -a; source /opt/aipc/scripts/load-secrets.sh; set +a

# Each service sources its own .env + starts uvicorn in background
declare -A PORTS=( ["executor"]=8091 ["watcher"]=8092 ["planner"]=8093 ["evaluator"]=8094 ["intake"]=8095 )

for svc in executor watcher planner evaluator intake; do
  port=${PORTS[$svc]}
  fuser -k "${port}/tcp" 2>/dev/null || true
  sleep 1
  set -a
  source /opt/aipc/conductor/services/${svc}/.env
  set +a
  cd /opt/aipc/conductor
  setsid uv run uvicorn services.${svc}.main:app \
    --host 0.0.0.0 --port "${port}" \
    > /tmp/${svc}-svc.log 2>&1 &
  echo "${svc}-svc started on :${port} (PID $!)"
done
```

### Clean state + restart (microservice)
```bash
bash /opt/aipc/conductor/scripts/clean_microservice_state.sh
```
One-shot: truncates all Conductor DB tables (including `outbox`, `processed_events`), cleans workspace dirs, purges RabbitMQ queues, kills service processes, cleans AionUi DB, then restarts all 5 microservices. Health check runs at end.

### Step-by-step: clean re-run a plan (after code or config changes)

Full teardown + restart sequence. Use when you changed evaluator code, rubric presets, agent configs, or event bus wiring and want a clean cycle.

```bash
# 0. Variables
PLAN_ID="plan_09f23fe0"
declare -A PORTS=( ["executor"]=8091 ["watcher"]=8092 ["planner"]=8093 ["evaluator"]=8094 ["intake"]=8095 )

# 1. Kill running microservices
for port in 8091 8092 8093 8094 8095; do
    fuser -k "${port}/tcp" 2>/dev/null || true
done

# 2. Clean Conductor DB (stale runs, node_sessions, outbox, processed_events)
docker exec postgres psql -U aipc -d aipc_conductor -c "
  DELETE FROM node_sessions;
  DELETE FROM runs;
  DELETE FROM processed_events;
  DELETE FROM outbox;
  UPDATE plans SET ratified = false;
"

# 3. Clean AionUi SQLite DB (purge stale conversations, messages, teams)
sqlite3 /home/aipc/.config/AionUi/aionui/aionui-backend.db "
    DELETE FROM messages;
    DELETE FROM conversations;
    DELETE FROM assistant_sessions;
    DELETE FROM team_tasks;
    DELETE FROM teams;
    VACUUM;
"

# 4. Clean conductor workspace dirs
find /opt/aipc/conductor/workspace -mindepth 1 -maxdepth 1 -type d -exec rm -rf {} +

# 5. Kill lingering opencode ACP processes
pkill -f "opencode acp" 2>/dev/null || true
sleep 1

# 6. Purge RabbitMQ queues (load secrets first for auth)
set -a; source /opt/aipc/scripts/load-secrets.sh; source /opt/aipc/conductor/.env; set +a
RABBIT_PASS="$(echo "$RABBIT_URL" | sed 's/.*:\([^@]*\)@.*/\1/')"
RABBIT_AUTH="conductor:${RABBIT_PASS}"
for queue in planner.q executor.q watcher.q evaluator.q; do
    curl -s -u "$RABBIT_AUTH" -X DELETE \
        "http://127.0.0.1:15672/api/queues/staging/${queue}/contents" > /dev/null
done

# 7. Restart all 5 microservices
for svc in executor watcher planner evaluator intake; do
    port=${PORTS[$svc]}
    set -a
    source /opt/aipc/scripts/load-secrets.sh 2>/dev/null
    source /opt/aipc/conductor/services/${svc}/.env
    set +a
    cd /opt/aipc/conductor
    setsid uv run uvicorn services.${svc}.main:app \
        --host 0.0.0.0 --port "${port}" \
        > /tmp/${svc}-svc.log 2>&1 &
    echo "${svc}-svc started on :${port} (PID $!)"
done

sleep 5

# 8. Health check
for p in 8091 8092 8093 8094 8095; do
    echo -n ":${p} "
    curl -sfm 3 "http://127.0.0.1:${p}/health" 2>/dev/null || echo "DOWN"
done

# 9. Re-ratify with 300s timeout
curl -s --max-time 300 -X POST "http://127.0.0.1:8093/ratify/${PLAN_ID}" \
  -H 'Content-Type: application/json' | python3 -m json.tool
```

### Manual E2E cycle
```bash
# 1. Submit goal (single project)
curl -s -X POST http://127.0.0.1:8093/goal \
  -H 'Content-Type: application/json' \
  -d '{"raw_input":"<goal>","project_id":"default"}'

# 2. Submit system goal (multi-project decomposition — returns proposal_id)
curl -s --max-time 300 -X POST http://127.0.0.1:8093/system/goal \
  -H 'Content-Type: application/json' \
  -d '{"raw_input":"<system goal>","families":null}'

# 3. Ratify system proposal (materialises system + projects + queues first goals)
curl -s -X POST http://127.0.0.1:8093/system/ratify/<proposal_id> \
  -H 'Content-Type: application/json' \
  -d '{}'

# 4. Clarify (if goal needs refinement — returns formulated MetaGoal, re-invokes graph)
curl -s -X POST http://127.0.0.1:8093/clarify/<plan_id> \
  -H 'Content-Type: application/json' \
  -d '{"clarification":"<your clarification text>","human_input":"<revised goal or spec>"}'

# 5. Ratify plan (use plan_id from step 1/4)
curl -s -X POST http://127.0.0.1:8093/ratify/<plan_id>

# 6. Stop active run (cancels run, pauses intake for project)
curl -s -X POST http://127.0.0.1:8093/stop \
  -H 'Content-Type: application/json' \
  -d '{"project_id":"<project_id>","reason":"<why>"}'

# 4. Monitor cycle
docker exec postgres psql -U aipc -d aipc_conductor \
  -c "SELECT id, run_id, node_id, verdict, gate_outcome, l2_score FROM node_sessions"
docker exec postgres psql -U aipc -d aipc_conductor \
  -c "SELECT * FROM outbox ORDER BY id"
docker exec postgres psql -U aipc -d aipc_conductor \
  -c "SELECT * FROM processed_events ORDER BY processed_at"
docker exec postgres psql -U aipc -d aipc_conductor \
  -c "SELECT id, finding_type, status, created_at FROM intake_findings ORDER BY created_at DESC"
docker exec postgres psql -U aipc -d aipc_conductor \
  -c "SELECT id, origin, source_ref, status FROM intake_intents ORDER BY created_at DESC LIMIT 10"
```

### Service logs
```bash
tail -f /tmp/executor-svc.log   # executor (:8091)
tail -f /tmp/watcher-svc.log    # watcher (:8092)
tail -f /tmp/planner-svc.log    # planner (:8093)
tail -f /tmp/evaluator-svc.log  # evaluator (:8094)
tail -f /tmp/intake-svc.log     # intake (:8095)
```

## L3 calibration
```bash
# Seed golden set for a node type (example-generated data)
cd /opt/aipc/conductor && uv run python scripts/seed_golden_backend_api.py

# Trigger calibration (via evaluator-svc)
curl -s -X POST http://127.0.0.1:8094/calibrate/backend_api

# Check judge trust
docker exec postgres psql -U aipc -d aipc_conductor \
  -c "SELECT node_type, agreement, mae, trusted, calibrated_at FROM judge_trust"
```

## L4 persona simulation (watcher-observed)
```bash
# Check L4 status on a run
docker exec postgres psql -U aipc -d aipc_conductor \
  -c "SELECT id, kind, state, l4_status, l4_structural FROM runs WHERE kind='l4' AND parent_run_id='<run_id>'"

# Check L4 node_session status
docker exec postgres psql -U aipc -d aipc_conductor \
  -c "SELECT id, run_id, role, verdict, backend, finished_at FROM node_sessions WHERE role='l4' ORDER BY created_at DESC"

# Re-emit run.completed event (for a fresh L4 cycle)
docker exec postgres psql -U aipc -d aipc_conductor \
  -c "DELETE FROM processed_events WHERE event_key LIKE '%<run_id>:run.completed%'"
docker exec postgres psql -U aipc -d aipc_conductor \
  -c "DELETE FROM node_sessions WHERE role='l4' AND run_id IN (SELECT id FROM runs WHERE kind='l4' AND parent_run_id='<run_id>')"
docker exec postgres psql -U aipc -d aipc_conductor \
  -c "DELETE FROM runs WHERE kind='l4' AND parent_run_id='<run_id>'"
docker exec postgres psql -U aipc -d aipc_conductor \
  -c "DELETE FROM processed_events WHERE event_key LIKE '%<run_id>:l4.findings%'"
docker exec postgres psql -U aipc -d aipc_conductor \
  -c "INSERT INTO outbox (routing_key, payload, contracts_version, created_at) VALUES ('run.completed', '{\"event_type\": \"run.completed\", \"run_id\": \"<run_id>\", \"plan_id\": \"<plan_id>\", \"product_type\": \"api\"}', '1.0', NOW())"

# Re-emit l4.findings for intake (after fixing adapter code)
docker exec postgres psql -U aipc -d aipc_conductor \
  -c "DELETE FROM processed_events WHERE event_key='<run_id>:l4.findings'"
docker exec postgres psql -U aipc -d aipc_conductor \
  -c "INSERT INTO outbox (routing_key, payload, contracts_version, created_at) VALUES ('l4.findings', '<payload_json>', '1.0', NOW())"

# Check AionUi conversation status
curl -s http://127.0.0.1:40937/api/conversations/<conv_id> | python3 -m json.tool

# Query AionUi SQLite for conversation messages
sqlite3 /home/aipc/.config/AionUi/aionui/aionui-backend.db \
  "SELECT type, position, status, substr(content,1,80) FROM messages WHERE conversation_id='<conv_id>' ORDER BY created_at"

# Inspect isolated L4 workspace residue/report
ls -la /opt/aipc/conductor/workspace/l4_runs/<run_id>/
cat /opt/aipc/conductor/workspace/l4_runs/<run_id>/l4_scratch/l4_report.md

# Verify intake created an improvement intent from L4 findings
docker exec postgres psql -U aipc -d aipc_conductor \
  -c "SELECT id, origin, source_ref, status, substring(intent_text,1,80) FROM intake_intents WHERE origin='l4_findings' ORDER BY created_at DESC"

# Watch L4 flow in evaluator log
tail -f /tmp/evaluator-svc.log | grep -i "l4\|findings\|structural"
```

## Ratchet experiment
```bash
# Fire ratchet.trigger event
docker exec postgres psql -U aipc -d aipc_conductor \
  -c "INSERT INTO outbox (routing_key, payload, contracts_version, created_at) VALUES ('ratchet.trigger', '{\"event_type\": \"ratchet.trigger\", \"agent_config_id\": \"<agent_config_id>\", \"node_type\": \"executor\"}', '1.0', NOW())"

# Check experiments and skill_mutations tables
docker exec postgres psql -U aipc -d aipc_conductor \
  -c "SELECT * FROM experiments ORDER BY created_at"
docker exec postgres psql -U aipc -d aipc_conductor \
  -c "SELECT * FROM skill_mutations ORDER BY created_at"
```

## Whole-stack DB trace
```bash
# Trace a complete run end-to-end
RUN_ID="run_0422fd60"
PLAN_ID="plan_2b419edc"

echo "=== Plan ==="
docker exec postgres psql -U aipc -d aipc_conductor \
  -c "SELECT plan_id, plan_status, ratified FROM plans WHERE plan_id='$PLAN_ID'"

echo "=== Run ==="
docker exec postgres psql -U aipc -d aipc_conductor \
  -c "SELECT id, plan_id, l4_status, l4_standalone, l4_acceptance FROM runs WHERE id='$RUN_ID'"

echo "=== Node Session ==="
docker exec postgres psql -U aipc -d aipc_conductor \
  -c "SELECT id, verdict, gate_outcome, l1_pass, l2_score FROM node_sessions WHERE run_id='$RUN_ID'"
docker exec postgres psql -U aipc -d aipc_conductor \
  -c "SELECT id, role, verdict, gate_outcome FROM node_sessions WHERE role='l4'"

echo "=== Intake Intents ==="
docker exec postgres psql -U aipc -d aipc_conductor \
  -c "SELECT id, origin, status, created_at FROM intake_intents ORDER BY created_at DESC LIMIT 10"

echo "=== Judge Trust ==="
docker exec postgres psql -U aipc -d aipc_conductor \
  -c "SELECT * FROM judge_trust"

echo "=== Golden Set ==="
docker exec postgres psql -U aipc -d aipc_conductor \
  -c "SELECT COUNT(*), split FROM golden_set GROUP BY split"

echo "=== Outbox Events ==="
docker exec postgres psql -U aipc -d aipc_conductor \
  -c "SELECT id, routing_key, published_at IS NOT NULL as published FROM outbox ORDER BY id"

echo "=== Processed Events ==="
docker exec postgres psql -U aipc -d aipc_conductor \
  -c "SELECT event_key, processed_at FROM processed_events ORDER BY processed_at"

echo "=== Experiments ==="
docker exec postgres psql -U aipc -d aipc_conductor \
  -c "SELECT COUNT(*) FROM experiments"

echo "=== Skill Mutations ==="
docker exec postgres psql -U aipc -d aipc_conductor \
  -c "SELECT COUNT(*) FROM skill_mutations"
```

## Intake-svc findings
```bash
# List open findings
curl -s http://127.0.0.1:8095/api/intake/findings | python3 -m json.tool

# Acknowledge a finding
curl -s -X POST http://127.0.0.1:8095/api/intake/findings/<id>/ack

# Query intake_findings table
docker exec postgres psql -U aipc -d aipc_conductor \
  -c "SELECT id, finding_type, status, project_id, substring(payload::text, 1, 80) FROM intake_findings ORDER BY created_at DESC LIMIT 20"
```

## Whole-stack reset (clean state)
```bash
bash /opt/aipc/conductor/scripts/clean_microservice_state.sh
```

## Profiles & skills (import pipeline)
```bash
# Import agent profiles from external repos (uses DB, idempotent)
cd /opt/aipc/conductor && uv run python scripts/import_profiles.py --verify

# Import only skills (skip agents, already imported):
cd /opt/aipc/conductor && uv run python scripts/import_profiles.py --skills-only --pin

# Resume from a specific batch (1-indexed, e.g. batch 8 after partial import):
cd /opt/aipc/conductor && uv run python scripts/import_profiles.py --skills-only --start-batch 8 --pin

# Render global skills + agents to opencode harness dirs
uv run python scripts/renderer.py                     # install global skills
uv run python scripts/renderer.py --agents            # also install imported agents
uv run python scripts/renderer.py --list-harnesses    # list registered renderers

# Check installed global skills/agents
ls ~/.config/opencode/skills/                         # global skills dir
ls ~/.config/opencode/agent/ | wc -l                  # count global agents

# Check per-skill folders on disk
ls /opt/aipc/conductor/skills_store/ | wc -l          # count individual skill dirs
ls /opt/aipc/conductor/skills_store/<skill_id>/       # SKILL.md + optional scripts/

# Check capability→skill mappings
docker exec postgres psql -U aipc -d aipc_conductor \
  -c "SELECT * FROM capability_skills ORDER BY capability"

# Check imported skills in DB
docker exec postgres psql -U aipc -d aipc_conductor \
  -c "SELECT skill_id, has_scripts, length(body) FROM skills WHERE source='imported' LIMIT 10"

# Check imported agent configs
docker exec postgres psql -U aipc -d aipc_conductor \
  -c "SELECT agent_config_id, source, new_capabilities FROM agent_configs WHERE source='imported' LIMIT 10"

# Verify worktree skills were installed (run after a node spawns)
ls /opt/aipc/conductor/workspace/<project>.<run-id>/.opencode/skills/
```

## Harness-add procedure
To add a new CLI harness later:
1. Implement `HarnessRenderer` subclass in `backend/skills.py` (`render_agent`, `render_skill`, `agents_dir`, `skills_dir`)
2. Register via `register(MyRenderer())` — adds to `RENDERERS` dict
3. Add harness tool profile in `backend/planning/capability/harness_profiles.py`
4. Add harness name to `backend_targets` on relevant agent configs
No re-import, no schema change.

## Stress test data setup
```bash
# Seed stress test capabilities + agent_configs (idempotent)
set -a; source /opt/aipc/scripts/load-secrets.sh; source /opt/aipc/conductor/.env; set +a
cd /opt/aipc/conductor && uv run python scripts/seed_stress_domains.py

# Generate 90 stress goals via free LiteLLM (requires LITELLM_KEY_PLANNING)
set -a; source /opt/aipc/scripts/load-secrets.sh; source /opt/aipc/conductor/.env; set +a
LITELLM_GATEWAY_KEY="$LITELLM_KEY_PLANNING" LITELLM_GATEWAY_URL="$LITELLM_BASE" \
  uv run python scripts/gen_stress_goals.py

# Verify stress data
docker exec postgres psql -U aipc -d aipc_conductor \
  -c "SELECT domain, scope, count(*) FROM stress_goals GROUP BY domain, scope ORDER BY domain, scope"
docker exec postgres psql -U aipc -d aipc_conductor \
  -c "SELECT name, family FROM capabilities ORDER BY name"
```

## Migrations
Database migrations are in `/opt/aipc/conductor/backend/migrations/`. Migration files:
- `v6_030_l4.sql` — adds L4 columns (`l4_status`, `l4_standalone`, `l4_acceptance`, `l4_reason`) to `runs` table and `needs_usage_sim` to `plans`
- `v6_040_family_array.sql` — migrates `capabilities.family` from TEXT to JSONB array, adds GIN index
- `v6_060_stress_goals.sql` — creates `stress_goals` table for generated heterogeneous stress test goals
- `v6_070_skills_store.sql` — adds `store_path`, `has_scripts`, `requires_setup` columns to `skills` table for per-skill folder storage
- `v6_080_plan_l2_raw_response.sql` — adds `plan_l2_raw_response` TEXT column to `plan_eval_detailed` table for raw LLM response capture
- `v6_090_runs_project_id.sql` — adds `project_id` column to `runs` table, backfills from plans, adds partial unique index for active-run-per-project constraint
- Run via: `docker exec -i postgres psql -U aipc -d aipc_conductor < backend/migrations/<filename>.sql`

## Environment
```bash
/opt/aipc/conductor/.env                # monolith configuration (DB, Neo4j, LLM)
/opt/aipc/conductor/services/*/.env     # per-microservice env overrides
```

## Worksystem (File 10 — publish-on-merge store + system L4)
```bash
# Worksystem layout
ls /opt/aipc/conductor/worksystem/repos/<system_id>/          # per-system repo (derived, gitignored)
cat /opt/aipc/conductor/worksystem/repos/<system_id>/index.json  # member manifest + compose inputs
cat /opt/aipc/conductor/worksystem/repos/<system_id>/compose.yml # regenerated compose
ls /opt/aipc/conductor/worksystem/repos/<system_id>/members/<project>/  # published RUN.md/workspace.json/_source.json

# Inspect a system's published state
git -C /opt/aipc/conductor/worksystem/repos/<system_id> log --oneline -5

# Re-trigger publish for a merged run (executor side)
docker exec postgres psql -U aipc -d aipc_conductor \
  -c "SELECT id, state, publish_status, publish_error FROM runs WHERE publish_status IS NOT NULL ORDER BY id DESC LIMIT 10"

# Run on-demand system L4 against the worksystem snapshot
curl -s -X POST http://127.0.0.1:8091/l4/trigger/system \
  -H 'Content-Type: application/json' -d '{"system_id":"<system_id>"}'

# L4 worktree snapshots (retained per L4_TAGS_RETAIN)
ls /opt/aipc/conductor/worksystem/worktrees/
git -C /opt/aipc/conductor/worksystem/repos/<system_id> tag -l "l4/run-*"

# Worksystem tests
cd /opt/aipc/conductor && uv run python -m pytest backend/tests/test_worksystem.py backend/tests/test_l4_isolated_execution.py backend/tests/test_workspace_manifest.py -q
```

## Repo structure (Repomix snapshot — read before scanning files)
See .memory/snapshots/conductor_repomix.md (do not re-scan the tree; consult this first).
