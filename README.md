# AIPC Conductor

Local-first, multi-CLI agent orchestration with a native desktop work surface.

Conductor is a control-plane orchestrator for AI agent teams. It manages
**projects**, **sessions**, and **plans** as first-class git entities, spawns
agents through real CLI harnesses (OpenCode, Hermes) into isolated worktrees,
watches their progress with deterministic signals, and evaluates their output
with a four-layer meta-evaluator (L1 deterministic → L2 rubric judge → L3 jury
calibration → L4 persona simulation) plus a regression ratchet.

```
Project = git repo   Session = git branch   Task = work unit   Trace = one CLI invocation = one "room"
```

## Highlights

- **Git-native identity.** Projects are repos, sessions are branches, work is
  tracked as plan DAGs of nodes. No drift between intent and history.
- **Native PTY work surface.** Spawned CLIs run in PTY-backed terminal tabs
  inside the Tauri desktop app; an external `gnome-terminal` detach mode is
  available for human debugging. A browser UI (`:3090`) serves as a read-mostly
  fallback.
- **Declarative routing.** Routing rules live in the database (JSONB), not in
  orchestration code. New agent patterns = new YAML, no engine changes.
- **Evaluator that gates real work.** L1 runs deterministic checks in the
  worktree (pytest, curl, py_compile). L2 scores output against pre-authored
  rubrics. L3 recalibrates the judge against a frozen human golden set. L4
  runs out-of-band persona simulations against the product black-box.
- **Ratchet, not regression.** Winning agent-config mutations are validated on
  a held-out set before apply; frozen boundaries (permissions, engine, model,
  golden set, budget, check commands) can never be mutated.
- **Event-driven microservices.** Planner, executor, watcher, evaluator, and
  intake communicate over RabbitMQ via a transactional outbox — reliability,
  no lost events.


## Measured results

The evaluator isn't just designed — one component has been calibrated against a
human-labelled golden set and improved through a controlled experiment.

| | |
|---|---|
| Golden set | 29 human-labelled goals (21 calibration / 7 held-out, `md5` split, sha256-pinned) |
| Baseline | node-estimation 46% · standard-selection 86% · clarification 82% |
| Bias found | 15 over-estimates, 0 under-estimates — systematic, not random |
| One bounded mutation | node-estimation 52% → 95% calibration, 71% → 100% held-out, guards held |
| Honest caveat | 27/29 labels sit in one range, so a constant predictor scores 97% — κ is near zero and the durable result is the bias correction, not the headline |
| Downstream check | estimate vs actual plan size: Pearson 0.798 |

**Full write-up:** [Calibrating an LLM Planner](https://app.notion.com/p/Calibrating-an-LLM-Planner-A-Ratchet-Experiment-That-Fixed-a-Systematic-Over-Estimation-3c1974f33a9380099b8cef3e0291fac6)


## Direction

The next phase decouples the evaluation layer from orchestration.

Planning and execution become abstract — any CLI harness (opencode, Codex,
Claude Code, OpenHands, domain workbenches) produces artifacts into a git
worktree. The evaluation layer becomes the product: it takes a worktree plus
a set of checks, grades them, records human labels, measures agreement
(Cohen's κ), and optimises the grader against that agreement.

The interface is a single check schema — `what`, `how_verified`, and the kind
of oracle that applies (deterministic / artifact inspection / usage). A planner
emits it; a grader consumes it. Neither needs to know about the other.

**Why:** orchestration is converging fast across the open-source ecosystem.
A grader calibrated to a specific human standard is not, and does not transfer
between people. That is the part worth building.

**What this repo remains:** the full control-plane implementation, the domain
standards, and the calibration experiment that produced the result above.


## Repository layout

| Path | Purpose |
|---|---|
| `backend/` | FastAPI monolith (`:8090`): plans, runs, worktrees, watcher, evaluator, ratchet, MCP servers |
| `services/` | Microservices: `planner` (`:8093`), `executor` (`:8091`), `watcher` (`:8092`), `evaluator` (`:8094`), `intake` (`:8095`), `ratchet` |
| `ui/` | React browser UI (`:3090`) |
| `gui/` | Tauri v2 desktop shell (embedded PTY via `tauri-plugin-pty` + xterm.js) |
| `shared/` `contracts/` `schemas/` | Cross-service models, event contracts, JSON schemas |
| `agent_configs/` | Agent configuration YAML (authoritative model selection per role) |
| `skills/` | Built-in skill definitions and golden task examples |
| `scaffolds_store/` | Project scaffolding templates |
| `docs/` | Architecture overview and ADRs |

## Core concepts

| Concept | Meaning |
|---|---|
| **Plan** | A directed graph of nodes (task / review / approval) with per-node checks |
| **Node** | One step in a plan; runs in an isolated git worktree |
| **Worktree** | Git worktree per node — the agent's sandbox |
| **Session** | A conversation with an agent, spanning one poll cycle |
| **Trace** | One CLI invocation; handoffs create new traces |
| **Watcher** | Polls git state + cheap DB signatures; declares a node terminal when stable |
| **Verdict** | The deterministic terminal decision from watcher polling signals |
| **Gate** | Pass/fail check at the end of a node's build file |
| **Ratchet** | One-way progress model: completed nodes can't be re-opened; quality scores drive agent-config experiments |
| **Golden set** | Frozen, human-curated labeled examples that anchor evaluator trust |
| **Held-out set** | Golden examples reserved to prove a mutation generalises (no overfitting) |

## Architecture

Conductor is a control plane, not an agent runtime. It delegates execution to
external CLI harnesses and observes them:

```
                 ┌──────────────────────────────────────────────┐
                 │                 Conductor                     │
                 │  FastAPI monolith (:8090) + Tauri GUI (desktop)│
                 │  browser UI (:3090)                           │
                 └───────┬──────────────┬──────────────┬────────┘
                         │ RabbitMQ     │              │
              ┌──────────▼──┐   ┌───────▼──────┐   ┌───▼──────────┐
              │ planner-svc │   │ executor-svc │   │  watcher-svc │
              │    :8093    │   │    :8091     │   │    :8092     │
              └─────────────┘   └───────┬──────┘   └──────┬───────┘
                                        │                 │
                              ┌─────────▼──────┐   ┌──────▼────────┐
                              │ evaluator-svc  │   │  intake-svc   │
                              │     :8094      │   │    :8095      │
                              └────────────────┘   └───────────────┘

   Execution harnesses:  OpenCode CLI  ·  Hermes Agent (Docker sandbox)
   Event transport:      RabbitMQ topic exchange `conductor.events`
                         via transactional outbox (no lost events)
   Product memory:       Neo4j knowledge graph per project (optional)
```

Evaluation pipeline per completed node:

```
watcher "done" ──► L1 deterministic checks (shell in worktree)
                     │ pass
                     ▼
                  L2 rubric judge (LLM, pre-authored rubrics)
                     │ score
                     ▼
                  L3 jury calibration (periodic, frozen golden set)
                     │ drift?
                     ▼
                  L4 persona simulation (out-of-band, black-box)
                     │ findings
                     ▼
                  Ratchet experiment (mine → propose → held-out validate)
```

## Getting started

> Conductor is currently a local-first tool. The fast path below assumes a
> Linux host with Docker (Postgres, RabbitMQ, optional Neo4j), a Python 3.11+
> toolchain via `uv`, and Node.js 20+ for the UIs.

### 1. Prerequisites

```bash
# System services (Postgres + RabbitMQ)
docker compose up -d

# Python toolchain
uv sync

# Node toolchain (UI + Tauri GUI)
cd ui && npm install
cd ../gui && npm install
```

### 2. Configure environment

```bash
cp .env.example .env
# fill in DATABASE_URL, RabbitMQ URL, LLM gateway keys, JWT secret, etc.
```

Apply migrations:

```bash
docker exec -i postgres psql -U <user> -d <db> < backend/migrations/<latest>.sql
```

### 3. Start the backend

```bash
uv run uvicorn backend.main:app --host 127.0.0.1 --port 8090
```

### 4. Start the microservices

Each service reads its own `services/<name>/.env` and runs on its port:

```bash
for svc in executor watcher planner evaluator intake; do
  set -a; source services/${svc}/.env; set +a
  setsid uv run uvicorn services.${svc}.main:app \
    --host 0.0.0.0 --port "${port}" &
done
```

### 5. Run the UIs

```bash
cd ui && npm run dev        # browser fallback, localhost:3090
cd gui && npm run tauri dev # native desktop shell with embedded PTY tabs
```

## Usage

Submit a goal to the planner service:

```bash
curl -s -X POST http://127.0.0.1:8093/goal \
  -H 'Content-Type: application/json' \
  -d '{"raw_input": "<goal>", "project_id": "<project>"}'
```

Clarify (if the planner asks) and ratify:

```bash
curl -s -X POST http://127.0.0.1:8093/clarify/<plan_id> \
  -H 'Content-Type: application/json' \
  -d '{"clarification": "<answer>", "human_input": "<revised goal or spec>"}'

curl -s -X POST http://127.0.0.1:8093/ratify/<plan_id>
```

From there the event bus takes over: executor spawns the node team, watcher
polls worktrees, evaluator gates each node, and intake converts findings into
improvement intents.

## Testing

```bash
# Backend test suite
uv run python -m pytest backend/tests/ -v

# Evaluator layers
uv run python -m pytest backend/tests/test_evaluator_schema.py -v
uv run python -m pytest backend/tests/test_evaluator_l1.py -v
uv run python -m pytest backend/tests/test_evaluator_l2.py -v
uv run python -m pytest backend/tests/test_ratchet_wiring.py -v
```

## Documentation

- [`docs/architecture.md`](docs/architecture.md) — architectural pillars and rejected alternatives
- [`docs/decisions.md`](docs/decisions.md) — architecture decision records (ADRs)
- `CONVENTIONS.md`, `GLOSSARY.md`, `DECISIONS.md`, `COMMANDS.md` — engineering conventions, domain glossary, locked decisions, and operational commands

## Security notes

- Runtime secrets live in `.env` / `services/*/.env` — never committed.
- MCP servers expose read + pending-create operations only; approval and
  spawn actions stay inside the Conductor UI.
- Evaluator gates are fail-open by design: infrastructure errors never block a
  node from committing.
