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

## L4 persona simulation
```bash
# Check L4 scores on a run
docker exec postgres psql -U aipc -d aipc_conductor \
  -c "SELECT id, l4_status, l4_standalone, l4_acceptance, l4_reason FROM runs WHERE id = '<run_id>'"

# Re-emit run.completed event (for L4 retry)
docker exec postgres psql -U aipc -d aipc_conductor \
  -c "DELETE FROM processed_events WHERE event_key LIKE '%<run_id>:run.completed%'"
docker exec postgres psql -U aipc -d aipc_conductor \
  -c "UPDATE runs SET l4_status = NULL, l4_standalone = NULL, l4_acceptance = NULL, l4_reason = NULL WHERE id = '<run_id>'"
docker exec postgres psql -U aipc -d aipc_conductor \
  -c "INSERT INTO outbox (routing_key, payload, contracts_version, created_at) VALUES ('run.completed', '{\"event_type\": \"run.completed\", \"run_id\": \"<run_id>\", \"plan_id\": \"<plan_id>\", \"product_type\": \"api\"}', '1.0', NOW())"

# Check AionUi conversation status
curl -s http://127.0.0.1:40937/api/conversations/<conv_id> | python3 -m json.tool

# Query AionUi SQLite for conversation messages
sqlite3 /home/aipc/.config/AionUi/aionui/aionui-backend.db \
  "SELECT type, position, status, substr(content,1,80) FROM messages WHERE conversation_id='<conv_id>' ORDER BY created_at"

# Inspect isolated L4 workspace residue/report
ls -la /opt/aipc/conductor/workspace/l4_runs/<run_id>/
cat /opt/aipc/conductor/workspace/l4_runs/<run_id>/l4_scratch/l4_report.md

# Verify isolated L4 real run result
docker exec postgres psql -U aipc -d aipc_conductor \
  -c "SELECT id, l4_status, l4_standalone, l4_acceptance, l4_reason FROM runs WHERE id='<run_id>'"
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

## Whole-stack reset (clean state)
```bash
bash /opt/aipc/conductor/scripts/clean_microservice_state.sh
```

## Profiles & skills (import pipeline)
```bash
# Import agent profiles from external repos (uses DB, idempotent)
cd /opt/aipc/conductor && uv run python scripts/import_profiles.py --verify

# Render global skills + agents to opencode harness dirs
uv run python scripts/renderer.py                     # install global skills
uv run python scripts/renderer.py --agents            # also install imported agents
uv run python scripts/renderer.py --list-harnesses    # list registered renderers

# Check installed global skills/agents
ls ~/.config/opencode/skills/                         # global skills dir
ls ~/.config/opencode/agent/ | wc -l                  # count global agents

# Check capability→skill mappings
docker exec postgres psql -U aipc -d aipc_conductor \
  -c "SELECT * FROM capability_skills ORDER BY capability"

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

## Migrations
Database migrations are in `/opt/aipc/conductor/backend/migrations/`. Migration files:
- `v6_030_l4.sql` — adds L4 columns (`l4_status`, `l4_standalone`, `l4_acceptance`, `l4_reason`) to `runs` table and `needs_usage_sim` to `plans`
- Run via: `docker exec -i postgres psql -U aipc -d aipc_conductor < backend/migrations/<filename>.sql`

## Environment
```bash
/opt/aipc/conductor/.env                # monolith configuration (DB, Neo4j, LLM)
/opt/aipc/conductor/services/*/.env     # per-microservice env overrides
```
