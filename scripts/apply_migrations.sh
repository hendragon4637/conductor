#!/usr/bin/env bash
set -euo pipefail

DB_NAME="${DB_NAME:-aipc_conductor}"
DB_USER="${DB_USER:-aipc}"
DB_HOST="${DB_HOST:-localhost}"

MIGRATIONS_DIR="$(dirname "$0")/../backend/db/migrations"

for f in "$MIGRATIONS_DIR"/*.sql; do
  echo "Applying $(basename "$f")..."
  docker exec -i postgres psql -U "$DB_USER" -h "$DB_HOST" -d "$DB_NAME" -v ON_ERROR_STOP=1 < "$f"
done

echo "All migrations applied."
