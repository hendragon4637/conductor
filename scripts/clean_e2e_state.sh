#!/bin/bash
# Clean all e2e state: conductor DB, AionUi DB, and workspace directories.
# Run before each e2e rerun.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONDUCTOR_DIR="$(dirname "$SCRIPT_DIR")"

echo "=== Cleaning e2e state ==="

# 1. Load env vars (without printing them)
set -a
source "$CONDUCTOR_DIR/.env" 2>/dev/null || true
source /opt/aipc/scripts/load-secrets.sh 2>/dev/null
set +a

# 2. Conductor DB (PostgreSQL)
echo "  Conductor DB..."
cd "$CONDUCTOR_DIR"
uv run python -c "
import os, psycopg
url = os.environ.get('DATABASE_URL', '')
if not url:
    print('    No DATABASE_URL — skipping')
else:
    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute('TRUNCATE session_signals, traces, node_sessions, tasks, runs, sessions, plans RESTART IDENTITY CASCADE')
            conn.commit()
            print('    OK')
" 2>/dev/null

# 3. AionUi DB (SQLite)
AIONUI_DB="${AIONUI_DB:-$HOME/.config/AionUi/aionui/aionui-backend.db}"
if [ -f "$AIONUI_DB" ]; then
    echo "  AionUi DB ($AIONUI_DB)..."
    sqlite3 "$AIONUI_DB" "
        DELETE FROM messages;
        DELETE FROM conversations;
        DELETE FROM assistant_sessions;
        DELETE FROM team_tasks;
        DELETE FROM cron_jobs;
        VACUUM;
    " 2>/dev/null && echo "    OK" || echo "    Failed to clean AionUi DB"
else
    echo "  AionUi DB not found at $AIONUI_DB — skipping"
fi

# 4. Conductor workspace (delete project dirs, keep workspace dir)
WORKSPACE_DIR="/opt/aipc/conductor/workspace"
if [ -d "$WORKSPACE_DIR" ]; then
    echo "  Workspace dirs..."
    find "$WORKSPACE_DIR" -mindepth 1 -maxdepth 1 -type d -exec rm -rf {} +
    echo "    OK"
else
    echo "  Workspace dir not found — skipping"
fi

echo "=== Cleanup complete ==="
