# Locked Architecture Decisions (append-only)

## 2026-06-11 — Node model = members-only, built-in AionUi orchestrator
Status: SUPERSEDED
Decision: Nodes specify members only; the AionUi orchestrator is built-in (single entry point, not configurable).
Supersedes: N/A
Superseded by: 2026-06-16 — Node entry point depends on team size

## 2026-06-11 — Three-tier control architecture
Status: ACTIVE
Decision: Conductor = control plane (DB + watcher + API) | AionUi = agent orchestration server | Langfuse = observability. Each runs independently, communicates via PostgreSQL and HTTP.

## 2026-06-11 — Deterministic watcher verdict (ratchet model)
Status: ACTIVE
Decision: Watcher polls cheap signals (git diff + DB query signature), waits for N consecutive stable polls (unchanged_cycles >= 2) before marking terminal. Prevents false "done" from intermediate agent text messages. No oracle/heuristic for terminal — stability is the signal.

## 2026-06-11 — Thread-scoped settings, DB-persisted chat
Status: ACTIVE
Decision: Chat threads stored in PostgreSQL (chat_threads, chat_messages). Model and project_ids scoped per thread. project_ids is JSONB array. thread_id is pure UUID (no longer encodes project name). DELETE and PUT endpoints for thread management.

## 2026-06-11 — Dict-based DB row access (dict_row pattern)
Status: ACTIVE
Decision: Use `dict(zip(keys, row))` pattern with explicit column names rather than relying on sqlite3.Row or psycopg2 DictCursor. All 3 call sites (list_threads, _load_thread, _load_messages) use this pattern.

## 2026-06-11 — Plan persistence survives restarts
Status: ACTIVE
Decision: save_plan() persists project_id/session_id even for draft plans. _get_or_load_plan() hydrates from DB on startup. list_plans merges DB + in-memory. Promoted plans use `plan-from-<project-slug>-<uuid8>` naming.

## 2026-06-11 — Memory management = files+git (meta), Graphiti (product), Obsidian (human)
Status: ACTIVE
Decision: Three separate tiers:
- Meta (dev memory): files + Git + Repomix — zero infra, boring, durable
- Product (per-project): Graphiti (Neo4j) temporal knowledge graph
- Human (your notes): Obsidian, local-only, never programmatic
Rationale: Different risk profiles need different tools. Meta memory is authored by us (small set) — no engine needed. Product memory spans many evolving projects with "what-was-true-when" — Graphiti's temporal graph earns the Neo4j cost.

## 2026-06-11 — Memory lifecycle: auto-capture, gated promotion, decay-forget
Status: ACTIVE
Decision: Capture runs per-session (batch LLM, not per-message). Consolidate dedups + importance-scores. Forget decays only session-scope noise (decisions durable). Promote to project/global is HUMAN-GATED (queued in UI, never auto-applied).

## 2026-06-11 — Meta-Evaluator: separation of detection vs evaluation
Status: ACTIVE
Decision: Watcher detects "did it finish" (deterministic). Evaluator judges "is it good" (L1 rules + L2 judge). Two separate components. The evaluator gate sits between watcher's "done" verdict and node commit — watcher is untouched.

## 2026-06-11 — Evaluator: L1 before L2, one generalist judge with preset rubrics
Status: ACTIVE
Decision: Deterministic checks (L1) run first — they filter obvious failures before spending LLM tokens. L2 uses one generalist judge engine with preset rubrics selectively applied per node type, not zero-shot rubric generation. Specialize the rubric, not the judge.

## 2026-06-11 — Evaluator: candidate checks generated at decompose, ratified at plan approval
Status: ACTIVE
Decision: Checks are generated assistively at decompose time (generate_checks from success criteria + rubric presets). They are candidates only — human must ratify them at plan approval (checks_ratified). Never silently mutate ratified checks; version them via checks_version.

## 2026-06-12 — Ratchet: consumes evaluator score, not watcher verdict
Status: ACTIVE
Decision: The ratchet optimises against the evaluator's Langfuse `goal_review` score (L2 rubric judge), not the watcher "done" verdict. The experiment runner replaces its old deterministic `_score_task` with evaluator `run_l2()`. This ensures ratchet optimises toward quality, not just completion.

## 2026-06-12 — Ratchet: frozen-boundary safety (probabilistic-only mutations)
Status: ACTIVE
Decision: The ratchet may ONLY mutate probabilistic artifacts (skill, agents_md, prompt, rubric wording, judge-prompt). It MUST NOT touch deterministic safety bounds (permissions, engine, model, golden set, budget caps, check_cmd). Enforcement is explicit in `_reject_frozen_target()` with structural keyword detection.

## 2026-06-12 — Ratchet: scope gating for global vs project mutations
Status: ACTIVE
Decision: Winning mutations on global-scope agent configs (domain=backend/general) are QUEUED for human approval — never auto-applied. Project-scope winners may auto-apply. Scope is inferred from `agent_configs.domain` via `detect_scope()`.

## 2026-06-12 — Ratchet: held-out validation for generalisation
Status: ACTIVE
Decision: Candidate mutations must beat baseline on a held-out validation set (not just the mining set where failures were found). If the candidate regresses on held-out tasks, it is reverted even if the overall delta is positive. This prevents overfitting to the mining set.

## 2026-06-12 — L3 meta-evaluation: golden set anchor + jury calibration
Status: ACTIVE
Decision: The L3 layer runs out-of-band (scheduled, not per-node) to periodically calibrate the L2 judge against a frozen human golden set. The golden set is stored in a dedicated `golden_set` table and is written ONLY by human action (`add_golden`). A diverse-family jury (≥2 model families) independently scores golden items; drift between L2 judge and human labels beyond tolerance (default 15%) triggers a rubric-refinement proposal queued for human approval — never auto-applied. The `v4_006_golden.sql` migration creates both `golden_set` and `rubric_refinements` tables.

## 2026-06-12 — L4 persona simulation: black-box usage UX friction detection
Status: ACTIVE
Decision: L4 runs conditionally (only when the product has a user-facing surface, e.g. APIs, UIs, CLIs) at the end of a plan or milestone — out-of-band like L3. An agent acts as a persona (defined in YAML) and performs goal-oriented behaviors against the running product via HTTP, recording friction observations per dimension (discoverability, error_feedback, friction). L4 produces a structured `L4Report` with scores per dimension; it NEVER auto-decides feature direction — the report is surfaced for human review. No `auto_apply` or `decision` field exists on the report.

## 2026-06-12 — Memory ↔ Evaluator integration: bidirectional learning loop
Status: ACTIVE
Decision: Memory grounds check generation (read direction) and evaluator findings persist as memories (write direction). Read: `ground_checks_with_memory()` searches Neo4j product memory for conventions and past error patterns before `generate_checks()` runs, injecting recalled knowledge as extra rubric items. Write: `capture_evaluator_findings()` writes failing L1/L2 items as MemoryFact nodes at session scope after gate decisions. Meta tier: `ground_meta_evaluation()` reads DECISIONS.md to detect plan violations against locked architecture decisions. Critical boundary: memory NEVER reads from or writes to the L3 golden set (frozen anchor). Promotion to global scope stays human-gated via the memory lifecycle's consolidate/promote path.

## 2026-06-12 — MCP server topology: AIPC serves, human PC consumes
Status: ACTIVE
Decision: Conductor and Obsidian vault are exposed as MCP servers via SSE transport on the AIPC's LAN IP, reached by Claude Desktop on the Windows human PC over the network. MCP servers bind to LAN interface (never 0.0.0.0/public), require bearer token auth, and expose only read + pending-create tools — no approve/spawn/delete/cancel. The external chat proposes through MCP; Conductor still validates against its plan spec and humans ratify in the UI. Obsidian is a read source only, never a driver.

## 2026-06-12 — Automated decomposition: memory-grounded check generation at plan time
Status: ACTIVE
Decision: `_decompose_from_intent()` calls `ground_checks_with_memory()` before the LLM prompt to recall product conventions, and per-node after chunk parsing for agent-specific past error patterns. Memory-grounded checks are injected as `extra_checks` into `generate_checks()`. The LLM prompt includes recalled conventions for informed DAG proposals. Both full decompose and incremental append paths use memory grounding. If Neo4j is unavailable, decomposition proceeds without memory context (graceful degradation).

## 2026-06-14 — Hermes execution-tier alongside AionUi
Status: ACTIVE
Decision: Hermes Agent (Nous Research v0.16.0) is a SECOND execution backend behind Conductor's adapter — along with AionUi. Conductor stays the control plane (plan, approve, evaluate, ratchet, git ladder). Hermes is execution-tier (AionUi's level), governed not duplicated. Integration via Hermes's HTTP API server (`run_submission` + `run_events_sse` + `run_stop`). Hermes owns intra-node execution (self-decomposes, routes to subagents). Conductor sends ONE goal per node. Self-evolution coupling = observe only — never reach into Hermes's internal skill store.

## 2026-06-14 — MCP tool naming: hyphens over dots
Status: ACTIVE
Decision: MCP tool names use hyphens (`-`) as namespace separators instead of dots (`.`). Examples: `conductor-create_plan` instead of `conductor.create_plan`, `obsidian-read_note` instead of `obsidian.read_note`. This avoids ambiguity in systems that interpret dots as attribute access or module paths.

## 2026-06-16 — Nvidia model for opencode backend executor
Status: ACTIVE
Decision: Changed `model_preference` from `minimax/minimax-m2.5:free` to `nvidia/gpt-oss-120b` in `opencode-backend-executor.yaml`. Removed the minimax→opencode/deepseek-v4-flash-free override in both `_normalize_model()` and `spawn_team()` in `spawn.py`. Agent config YAML `model_preference` is now the authoritative source for the model passed to AionUi on spawn.

## 2026-06-16 — Sequential capstone execution via PLANS_SELECTION
Status: ACTIVE
Decision: Capstone plans run one at a time controlled by `PLANS_SELECTION` env var (comma-separated plan IDs). Hermes (Plan D) is skipped due to `hermes acp` crashing with PermissionError on stdin in headless mode. After each plan completes, the next can be triggered by re-running with a different selection. C2 uses PLANS_SELECTION to decide which runs to create/approve/start; C3-C6 skip if their plan is not in the selection.

## 2026-06-14 — MCP auth middleware: app.add_middleware() pattern
Status: ACTIVE
Decision: Token auth middleware uses `app.add_middleware(TokenCheckMiddleware)` on the Starlette SSE app — not Starlette-reconstruction with `on_startup`/`on_shutdown` (which fails on FastMCP SSE apps whose Router lacks those attributes). Both MCP servers (Conductor, Obsidian) use the same pattern.

## 2026-06-16 — Node entry point depends on team size
Status: ACTIVE
Decision: Node specs still declare members-only intent, but runtime entry points now vary by execution shape. Class-a backends remain self-orchestrating. For AionUi class-b nodes, single-member nodes spawn as one-agent teams with that member as the lead and no built-in AionUi orchestrator; multi-member nodes retain the built-in orchestrator lead. This keeps simple nodes direct while preserving coordination for true teams.

## 2026-06-16 — Obsidian repomix export is chunked
Status: ACTIVE
Decision: Repomix remains the canonical repo-pack snapshot in `.memory/snapshots/conductor_repomix.md`, and a second export is written into `/home/aipc/conductor-notes/research/repomix/` using chunked markdown files via `--split-output`. Obsidian consumes the chunked export for readability; the repo-local single file remains the authoritative packed snapshot for tooling.

## 2026-06-16 — Backend e2e runtime = single backend process on :8090
Status: ACTIVE
Decision: The normal Conductor backend/e2e flow runs through `backend.main:app` on `127.0.0.1:8090`, and that process owns watcher startup. Operational docs and restart commands should not reference nonexistent `run_backend.py` or a separate `run_watcher.py` for standard backend/e2e execution.

## 2026-06-16 — Evaluator config prefers NVIDIA for gpt-oss-120b
Status: ACTIVE
Decision: Where `gpt-oss-120b` is configured in Conductor-owned model selection, prefer the NVIDIA provider/config path rather than OpenCode Zen or unrelated hosted defaults. This affects brain/evaluator configuration and supporting documentation; executor spawn still follows per-agent YAML `model_preference`.

## 2026-06-18 — Multi-node advancement: pre-create node_sessions for all DAG nodes
Status: ACTIVE
Decision: `launch_run()` now creates `node_sessions` for ALL DAG nodes upfront at run start, not just the first node. The root node gets `verdict='running'`; subsequent nodes get `verdict='pending'`. This eliminates the bug where `_complete_and_advance()` could not find a `node_session` for watcher-spawned subsequent nodes (causing `NOT NULL` violations on `run_id` and `backend`).

Supporting changes:
- `_next_ready_node()` checks `node_sessions` (not `tasks`) for done/running status — the old `tasks`-based logic would skip all pre-created `pending` sessions.
- `_complete_and_advance()` sets the next node's verdict to `'running'` before spawning (via `UPDATE ... RETURNING id`) and fixes `st.node_session_id`.
- The `all_done` check uses `node_sessions.verdict` instead of `tasks.status`, since `tasks` is not populated for watcher-spawned nodes.
- A cleanup script is at `/tmp/clean_conductor.sh` for resetting Conductor DB + AionUi + workspace.

## 2026-06-19 — L3 calibration: golden-set anchored jury, periodic drift detection
Status: ACTIVE
Decision: L3 calibration runs periodically (not in the hot path) to detect drift between the L2 judge's scores and the frozen human golden set. It re-scores golden artifacts via the L2 judge, computes MAE and agreement, upserts `judge_trust`, and surfaces drift. Calibration never auto-applies rubric edits — it queues `pending` proposals for human review.

## 2026-06-19 — Ratchet: frozen-boundary safety, scope-gated mutations, held-out validation
Status: ACTIVE
Decision: The ratchet loop enforces a frozen boundary: `permissions`, `allowed_tools`, `model_preference`, `check_cmd`, `golden_set`, `budget` may never be mutated by automation. Only probabilistic fields (`system_prompt`, `skill`, `rubric_wording`, `judge_prompt`, `brief`) are editable. Mutations that win on the mining set must validate without regression on a held-out split before auto-application. Global-scope mutations (domain=backend/general) always queue for human approval; project-scope mutations may auto-apply.

## 2026-06-19 — Plan evaluator: structural L1 + plan-rubric L2 for pre-execution gating
Status: ACTIVE
Decision: Plan evaluation runs at ratification time, before execution. L1 checks the DAG structure (nodes≥1, fields present, deps resolve, acyclic). L2 applies a `plan_structure.yaml` rubric via the L2 judge if L1 passes. The `plan_goal_review` score is stored on the run — it gates plan approval, not node execution. If the L2 judge is unavailable, L1 alone determines the verdict (fail-open).

## 2026-06-19 — L4 two-case simulation: standalone + acceptance with common engine
Status: ACTIVE
Decision: L4 runs in two cases — `l4_standalone` (persona session with derived scenario, no plan context) and `l4_acceptance` (same persona+rubric verifying plan.success as the closing gate). Both use the same engine and persona; only the framing differs. The driver is selected by product type (web→browser, cli→shell, api→http, doc→none). L4 reports are observational only — friction scores are surfaced for human review with no auto-decide mechanism.
