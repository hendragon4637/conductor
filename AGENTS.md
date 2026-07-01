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
| **Meta-Evaluator** | Quality gate system between watcher "done" verdict and node commit. Four layers: L1 deterministic, L2 rubric judge, L3 jury meta-eval, L4 persona simulation. |
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
| **L4 (persona simulation)** | Fourth evaluator layer: an agent uses the finished product as a user would (black-box) and reports UX/feature friction. Runs conditionally out-of-band for products with user-facing surfaces. |
| **Persona (L4)** | A YAML-defined user archetype with a goal, behaviors (action sequences), and report dimensions. Defines what to try and what to expect. |
| **L4Report** | Structured output from an L4 persona run: per-behavior results, per-dimension friction scores (0.0-1.0), and overall friction. Contains no auto-decide mechanism — observations only. |
| **MCP (Model Context Protocol)** | Protocol for exposing tools and resources to LLM applications. Conductor and Obsidian vault are MCP servers over SSE transport. |
| **SSE transport** | Server-Sent Events transport for MCP — used when client and server are on different machines (human PC → AIPC). |
| **Conductor MCP** | MCP server on `127.0.0.1:8092` exposing safe plan operations (`conductor-create_plan`, `conductor-refine_plan`, `conductor-get_plan`, `conductor-list_sessions`, `conductor-search_memory`). No approve/spawn. |
| **Obsidian vault MCP** | MCP server on `127.0.0.1:8093` exposing `/home/aipc/conductor-notes/` as read-only markdown resources via `obsidian-read_note`. |
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
| **EventBus** | RabbitMQ topic exchange (`conductor.events`) with per-service durable queues (`planner.q`, `executor.q`, `watcher.q`, `evaluator.q`). Each queue binds to routing keys matching the events its service consumes. |
| **Transactional outbox** | Reliability pattern: events are written to an `outbox` table atomically with the business DB transaction. A background relay thread (`relay_loop()`) publishes pending outbox rows to RabbitMQ and sets `published_at`. Services deduplicate via `processed_events` on consume. |
| **Planner-svc** | Microservice on `:8093` — accepts `POST /goal`, `POST /clarify/{id}`, `POST /ratify/{id}`. Runs the planner graph (`formulate → inject → decompose → select_capabilities → generate_checks → gate`). Emits `plan.ratified`. |
| **Executor-svc** | Microservice on `:8091` — consumes `plan.ratified`, calls monolith's `launch_run()` to create worktrees and spawn AionUi teams. Emits `node.spawned`. Consumes `gate.evaluated` (finalize or advance DAG) and `node.remediate` (fix-forward retry). |
| **Watcher-svc** | Microservice on `:8092` — consumes `node.spawned`, polls worktrees via `_watch_loop()` (30s interval, 30s settle, 2 stable poll cycles). Sets `verdict=done_no_change` or `failed`. Emits `node.observed`. |
| **Evaluator-svc** | Microservice on `:8094` — consumes `node.observed`, runs L1 deterministic checks then L2 rubric judge. Emits `gate.evaluated` with outcome `done`/`remediate`/`failed`. Optionally emits `node.remediate` for retry. |
| **ServiceConfig** | Pydantic model (`shared.config`) loaded from environment per microservice. Fields: `service`, `env`, `rabbit_url`, `database_url`. Each service's `.env` overrides defaults. |
| **Outbox relay** | Background daemon thread per service that polls the `outbox` table for unpublished rows and publishes them to RabbitMQ. Uses its OWN pika connection — sharing the consumer channel corrupts the AMQP frame stream. |
| **Planner graph** | The LangGraph-based planning flow: `formulate → inject → decompose → select_capabilities → generate_checks → gate`. Defined in `services/planner/graph.py`. The `formulate` node converts raw goal to MetaGoal; `inject` enriches with domain conventions; `decompose` produces a DAG of nodes; `select_capabilities` assigns agent_configs per node via the capability selector; `generate_checks` creates L1/L2 checks per node; `gate` runs `run_plan_gate()` for L1+L2 plan evaluation. |
| **Conditional entry point** | `_route_entry()` in the planner graph that checks `state["status"]` to route to `"formulate"` (new plan, `status=="new"`) or `"inject"` (formulated/clarified plan, `status=="formulated"`). Replaces `set_entry_point("formulate")` to enable the clarify→continue flow. |
| **Clarify → continue flow** | The `/clarify` endpoint in `services/planner/main.py` re-invokes the planner LangGraph after `formulate_or_clarify()` returns a MetaGoal, enabling formulated plans to proceed through `inject → decompose → select_capabilities → generate_checks → gate` without manual re-submission. Previously, `/clarify` returned `formulated` status but never continued the graph. |
| **Revise loop (planner remediation)** | When the planner gate fails, `_n_decompose()` passes `gate_feedback` and prior `dag` from `PlanState` to `decompose()`, which injects them into the LLM prompt as a `{revision_block}` with fix-forward instructions. The LLM keeps working nodes and only fixes failures, rather than regenerating the entire DAG from scratch. |

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
- RabbitMQ topology: topic exchange `conductor.events`, durable queues per service, bindings in `BINDINGS` dict in `shared/bus.py`.
- When consuming monolith functions (e.g. `launch_run()`) that use UPSERT, verify all columns are in the `ON CONFLICT ... DO UPDATE SET` clause. The monolith's `save_node_session()` omits `worktree` — patch node_sessions directly after calling `launch_run()`.
- Gate outcome values: evaluator emits `gate_outcome=done` on pass, but executor's `_handle_gate_evaluated` switches on `pass`/`fail`. Non-match falls through to "advance next node" — no finalize or quarantine fires. Both `done` and `pass`, and `fail` and `failed` are now handled.
- The monolith watcher (`get_watcher()`) is also initialized inside executor-svc when `launch_run()` calls it. This creates a separate watcher polling in the executor process alongside the microservice watcher-svc — harmless but creates duplicate state.
- NEVER have multiple pika consumers on the same queue. RabbitMQ round-robins messages across consumers regardless of routing key. Use a SINGLE dispatcher consumer that routes by event type (detected from payload fields).
- Before calling `finalize_success()`, always auto-commit the worktree via `git add -A && git commit`. The agent writes files to the worktree but never commits them; the merge requires committed changes.

## Git
- Atomic commits with clear messages
- Never commit .env, *.db, node_modules/, __pycache__/
- No force push to main

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

### Restart all 4 microservices
```bash
# Load secrets once
set -a; source /opt/aipc/scripts/load-secrets.sh; set +a

# Each service sources its own .env + starts uvicorn in background
for svc in executor watcher planner evaluator; do
  fuser -k "809${svc}/tcp" 2>/dev/null || true
  sleep 1
  set -a
  source /opt/aipc/conductor/services/${svc}/.env
  set +a
  cd /opt/aipc/conductor
  setsid uv run uvicorn services.${svc}.main:app \
    --host 0.0.0.0 --port 809${svc} \
    > /tmp/${svc}-svc.log 2>&1 &
  echo "${svc}-svc started on :809${svc} (PID $!)"
done
```

Port mapping: executor=8091, watcher=8092, planner=8093, evaluator=8094.

### Clean state + restart (microservice)
```bash
bash /opt/aipc/conductor/scripts/clean_microservice_state.sh
```
One-shot: truncates all Conductor DB tables (including `outbox`, `processed_events`), cleans workspace dirs, purges RabbitMQ queues, kills service processes, cleans AionUi DB, then restarts all 4 microservices. Health check runs at end.

### Manual E2E cycle
```bash
# 1. Submit goal
curl -s -X POST http://127.0.0.1:8093/goal \
  -H 'Content-Type: application/json' \
  -d '{"raw_input":"<goal>","project_id":"default"}'

# 2. Clarify (if goal needs refinement — returns formulated MetaGoal, re-invokes graph)
curl -s -X POST http://127.0.0.1:8093/clarify/<plan_id> \
  -H 'Content-Type: application/json' \
  -d '{"clarification":"<your clarification text>","human_input":"<revised goal or spec>"}'

# 3. Ratify (use plan_id from step 1)
curl -s -X POST http://127.0.0.1:8093/ratify/<plan_id>

# 4. Monitor cycle
docker exec postgres psql -U aipc -d aipc_conductor \
  -c "SELECT id, run_id, node_id, verdict, gate_outcome, l2_score FROM node_sessions"
docker exec postgres psql -U aipc -d aipc_conductor \
  -c "SELECT * FROM outbox ORDER BY id"
docker exec postgres psql -U aipc -d aipc_conductor \
  -c "SELECT * FROM processed_events ORDER BY processed_at"
```

### Service logs
```bash
tail -f /tmp/executor-svc.log   # executor
tail -f /tmp/watcher-svc.log    # watcher
tail -f /tmp/planner-svc.log    # planner
tail -f /tmp/evaluator-svc.log  # evaluator
```

## Environment
```bash
/opt/aipc/conductor/.env                # monolith configuration (DB, Neo4j, LLM)
/opt/aipc/conductor/services/*/.env     # per-microservice env overrides
```

## Repo structure (Repomix snapshot — read before scanning files)
See .memory/snapshots/conductor_repomix.md (do not re-scan the tree; consult this first).
