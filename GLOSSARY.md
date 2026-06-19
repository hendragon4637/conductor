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
