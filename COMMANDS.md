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
Database migrations are in `/opt/aipc/conductor/backend/migrations/`. New migration files prefixed with incrementing number + description (e.g., `011_fix_verdict_defaults.sql`). Run manually via psql with:
```bash
docker exec -i postgres psql -U aipc -d aipc_conductor < backend/migrations/<filename>.sql
```

## E2E test
```bash
cd /opt/aipc/conductor && uv run python scripts/e2e_l2_test.py
```
Cleans state: `bash /opt/aipc/conductor/scripts/clean_e2e_state.sh`

## Environment
```bash
/opt/aipc/conductor/.env           # configuration (DB, Neo4j, LLM provider)
```
