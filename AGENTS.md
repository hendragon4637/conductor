# Conductor — assembled context (do not edit; regenerate via .memory/assemble.sh)

## Locked decisions (ACTIVE only)
## 2026-06-11 — Three-tier control architecture
## 2026-06-11 — Deterministic watcher verdict (ratchet model)
## 2026-06-11 — Thread-scoped settings, DB-persisted chat
## 2026-06-11 — Dict-based DB row access (dict_row pattern)
## 2026-06-11 — Plan persistence survives restarts
## 2026-06-11 — Memory management = files+git (meta), Graphiti (product), Obsidian (human)
## 2026-06-11 — Memory lifecycle: auto-capture, gated promotion, decay-forget
## 2026-06-11 — Meta-Evaluator: separation of detection vs evaluation
## 2026-06-11 — Evaluator: L1 before L2, one generalist judge with preset rubrics
## 2026-06-11 — Evaluator: candidate checks generated at decompose, ratified at plan approval
## 2026-06-12 — Ratchet: consumes evaluator score, not watcher verdict
## 2026-06-12 — Ratchet: frozen-boundary safety (probabilistic-only mutations)
## 2026-06-12 — Ratchet: scope gating for global vs project mutations
## 2026-06-12 — Ratchet: held-out validation for generalisation
## 2026-06-12 — L3 meta-evaluation: golden set anchor + jury calibration
## 2026-06-12 — L4 persona simulation: black-box usage UX friction detection
## 2026-06-12 — Memory ↔ Evaluator integration: bidirectional learning loop
## 2026-06-12 — MCP server topology: AIPC serves, human PC consumes
## 2026-06-12 — Automated decomposition: memory-grounded check generation at plan time
## 2026-06-14 — Hermes execution-tier alongside AionUi
## 2026-06-14 — MCP tool naming: hyphens over dots
## 2026-06-16 — Nvidia model for opencode backend executor
## 2026-06-16 — Sequential capstone execution via PLANS_SELECTION
## 2026-06-14 — MCP auth middleware: app.add_middleware() pattern
## 2026-06-16 — Node entry point depends on team size
## 2026-06-16 — Obsidian repomix export is chunked
## 2026-06-16 — Backend e2e runtime = single backend process on :8090
## 2026-06-16 — Evaluator config prefers NVIDIA for gpt-oss-120b

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

## Evaluator (meta-evaluator)
- Evaluator sits between watcher "done" verdict and node commit — NEVER modify watcher for evaluation
- L1 deterministic checks run first (cheap, no LLM); L2 rubric judge only if L1 passes
- Checks are generated at decompose time via `generate_checks()`, ratified by human at plan approval
- Remediation nodes reuse `decompose_or_update("append_node")` lifecycle — no new orchestration
- L1 runs shell commands in the node worktree; exit 0 = pass
- Rubrics come from preset library, never zero-shot generated
- Evaluator gate fail-open: if L1 itself errors, node still commits (never block on evaluator infra)
- L3 (meta-eval) runs out-of-band, NOT in the hot path — scheduled periodically (e.g. weekly)
- L3 golden set is FROZEN and human-only; nothing in the pipeline writes `golden_set` automatically
- L3 jury must use ≥2 different model families; single-family fallback documents the limitation in the `note` field
- L3 drift → rubric refinement proposals are QUEUED with `status='pending'`, never auto-applied
- L4 runs conditionally only when the product has a user-facing surface (`needs_usage_sim`)
- L4 executes behaviors as HTTP requests against a running product server (black-box, no source reading)
- L4 produces structured friction scores per dimension; report is surfaced for human review — NEVER auto-decides feature direction
- L4 `L4Report` has no `auto_apply` or `decision` field — it carries observations only

## Ratchet
- Ratchet consumes the evaluator's `goal_review` Langfuse score, NOT the watcher verdict — experiment scoring uses `run_l2()`
- Frozen-boundary enforcement: ratchet may ONLY mutate probabilistic artifacts (skill, agents_md, prompt, rubric). Structural markers (`:` / `=`) distinguish config values from natural language mentions
- Scope gating: global-scope winners (domain=backend/general) are QUEUED for human approval; project-scope winners may auto-apply
- Held-out validation: candidate mutations must not regress on held-out tasks — overfitting to mining set causes revert
- Failure mining reads Langfuse `goal_review` score comments (format: `check_id: FAIL (explanation)`) to cluster recurring rubric failures

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

## Key commands
# Key Commands (Conductor development)

## Backend
```bash
cd /opt/aipc/conductor && uv run uvicorn backend.main:app --host 127.0.0.1 --port 8090
```

Notes:
- The FastAPI backend listens on `127.0.0.1:8090`
- The watcher starts inside `backend.main:app` on startup; do not start a separate `run_watcher.py` process

## Restart (setsid, from start-services.sh pattern)
```bash
# Kill existing backend first
pkill -f "uvicorn backend.main:app" 2>/dev/null || true
sleep 1

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
Database migrations are in `/opt/aipc/conductor/backend/migrations/`. New migration files prefixed with incrementing number + description (e.g., `011_fix_verdict_defaults.sql`). Run manually via psql.

## Environment
```bash
/opt/aipc/conductor/.env           # configuration (DB, Neo4j, LLM provider)
```

## Repo structure (Repomix snapshot — read before scanning files)
See .memory/snapshots/conductor_repomix.md (do not re-scan the tree; consult this first).
