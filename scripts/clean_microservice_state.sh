#!/bin/bash
# Clean all microservice e2e state: Conductor DB + workspace + pending outbox.
# Run before each microservice E2E rerun.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONDUCTOR_DIR="$(dirname "$SCRIPT_DIR")"

echo "=== Cleaning microservice e2e state ==="

# 1. Load env vars
set -a
source "$CONDUCTOR_DIR/.env" 2>/dev/null || true
source /opt/aipc/scripts/load-secrets.sh 2>/dev/null
set +a

# 2. Kill running microservices
echo "  Stopping services..."
for port in 8090 8091 8092 8093 8094; do
    fuser -k "${port}/tcp" 2>/dev/null && echo "    Killed port ${port}" || true
done

# 3. Conductor DB — clean ALL tables including outbox/processed_events
echo "  Conductor DB..."
cd "$CONDUCTOR_DIR"
uv run python -c "
import os, psycopg
url = os.environ.get('DATABASE_URL', '')
if not url:
    print('    No DATABASE_URL — skipping')
else:
    tables = [
        'session_signals', 'traces', 'node_sessions', 'tasks',
        'runs', 'sessions', 'plans', 'outbox', 'processed_events',
        'experiments', 'skill_mutations', 'judge_trust',
    ]
    with psycopg.connect(url) as conn:
        conn.autocommit = False
        with conn.cursor() as cur:
            for t in tables:
                try:
                    cur.execute(f'TRUNCATE {t} RESTART IDENTITY CASCADE')
                except Exception as e:
                    conn.rollback()
                    print(f'    SKIP {t}: {e}')
                    conn.autocommit = False
                    continue
            conn.commit()
            print('    OK — truncated: ' + ', '.join(tables))
" 2>/dev/null

# 4. Conductor workspace (delete project dirs, keep workspace dir)
WORKSPACE_DIR="/opt/aipc/conductor/workspace"
if [ -d "$WORKSPACE_DIR" ]; then
    echo "  Workspace dirs..."
    find "$WORKSPACE_DIR" -mindepth 1 -maxdepth 1 -type d -exec rm -rf {} +
    echo "    OK"
else
    echo "  Workspace dir not found — skipping"
fi

# 5. AionUi DB (SQLite) — clean stale conversations, messages, teams
AIONUI_DB="${AIONUI_DB:-$HOME/.config/AionUi/aionui/aionui-backend.db}"
if [ -f "$AIONUI_DB" ]; then
    echo "  AionUi DB..."
    sqlite3 "$AIONUI_DB" "
        DELETE FROM messages;
        DELETE FROM conversations;
        DELETE FROM assistant_sessions;
        DELETE FROM team_tasks;
        DELETE FROM teams;
        VACUUM;
    " 2>/dev/null && echo "    OK" || echo "    Failed to clean AionUi DB"
else
    echo "  AionUi DB not found at $AIONUI_DB — skipping"
fi

# 6. Purge RabbitMQ queues (optional — clears pending messages)
echo "  RabbitMQ queues..."
RABBIT_URL="${RABBIT_URL:-amqp://conductor:placeholder@127.0.0.1:5672}"
RABBIT_API="http://127.0.0.1:15672/api"
RABBIT_AUTH="conductor:$(echo "$RABBIT_URL" | sed 's/.*:\([^@]*\)@.*/\1/')"
for queue in planner.q executor.q watcher.q evaluator.q; do
    curl -s -u "$RABBIT_AUTH" -X DELETE "${RABBIT_API}/queues/staging/${queue}/contents" 2>/dev/null \
        && echo "    Purged ${queue}" || echo "    Failed to purge ${queue}"
done

# 7. Restart all microservices
echo "  Restarting services..."
declare -A PORTS=( ["executor"]=8091 ["watcher"]=8092 ["planner"]=8093 ["evaluator"]=8094 )
for svc in executor watcher planner evaluator; do
    port=${PORTS[$svc]}
    # Re-source per-service env
    set -a
    source /opt/aipc/scripts/load-secrets.sh 2>/dev/null
    source "$CONDUCTOR_DIR/services/${svc}/.env"
    set +a
    cd "$CONDUCTOR_DIR"
    setsid uv run uvicorn services.${svc}.main:app \
        --host 0.0.0.0 --port "${port}" \
        > /tmp/${svc}-svc.log 2>&1 &
    echo "    ${svc}-svc started on :${port} (PID $!)"
done

sleep 4
echo "=== Done. Health check: ==="
for p in 8091 8092 8093 8094; do
    echo -n "  :$p "
    curl -sfm 3 "http://127.0.0.1:$p/health" 2>/dev/null || echo "DOWN"
done
echo ""
