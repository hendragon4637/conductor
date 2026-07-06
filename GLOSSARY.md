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
| **Heterogeneity stress test** | A generated-data test proving the moat machinery works across multiple domains (Software Delivery + Content Studio) with varying verification strength. Covers: JSONB family-array selector, both backends (opencode + Hermes), L1/L2/L3/L4 evaluator layers, and the ratchet. |
| **Family array** | The `capabilities.family` column as a JSONB array of strings (e.g., `["software", "design"]`). Enables multi-domain capability matching via the `?|` overlap operator. Replaced the original single-string TEXT column. |
| **Stress goals** | Generated goals in the `stress_goals` table with `domain`, `scope` (small/medium/large), `title`, `spec`, and `expected_capabilities` — used for heterogeneity stress test execution. 90 goals total (45 domain × 15 scope). |
| **Provisional label** | A golden set label authored by a STRONGER model (ChatGPT Plus) rather than a human. Marked `labeled_by=chatgpt-plus`, `confidence=provisional`. Better than P0 (labeler ≠ judge breaks circularity), not as good as P3 (human = real ground truth). Swappable to human with zero system change (same `add_golden`, different `labeled_by`). |
| **Verification tier** | The strength of objective evaluation a capability supports: **strong-oracle** (backend_api, tests_suite — L1 deterministically verifiable), **mixed** (frontend, design_layout — L1 builds + L2 subjective), **weak-oracle** (copywriting, content_review — L1 file-exists only, L2 dominates), **unrealizable** (image_gen, music_generation — unsupported tools, honest skip). |
| **Skills store** | Per-skill directory under `skills_store/<skill_id>/` containing a `SKILL.md` and optionally a `scripts/` subdirectory. Each skill is fetched individually from its source GitHub repo, written to disk, then upserted to the DB with `store_path` pointing to its folder. |
| **Import pipeline** | The automated process of cloning external repos (agency-agents, wshobson, awesome-agent-skills), parsing to neutral rows, classifying capabilities, and inserting to DB with `source='imported'`. Skills are fetched sequentially (2s gap between requests) in interleaved batches of 30: fetch → LLM classify → upsert → next batch. |
| **Harness renderer** | A `HarnessRenderer` subclass in `backend/skills.py` that converts neutral DB rows to harness-specific config files. The opencode renderer copies per-skill folders from `skills_store/` to `~/.config/opencode/skills/` (global) or `.opencode/skills/` (worktree). |
