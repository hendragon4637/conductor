#!/usr/bin/env bash
set -euo pipefail

DB_NAME="${DB_NAME:-aipc_conductor}"
DB_USER="${DB_USER:-aipc}"
DB_HOST="${DB_HOST:-localhost}"

MIGRATIONS_DIR="$(dirname "$0")/../backend/db/migrations"

# Ensure tracking table exists
docker exec -i postgres psql -U "$DB_USER" -h "$DB_HOST" -d "$DB_NAME" -v ON_ERROR_STOP=1 <<'EOSQL'
CREATE TABLE IF NOT EXISTS schema_migrations (
  filename TEXT PRIMARY KEY,
  applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
EOSQL

for f in "$MIGRATIONS_DIR"/*.sql; do
  name="$(basename "$f")"

  already_applied=$(docker exec postgres psql -U "$DB_USER" -h "$DB_HOST" -d "$DB_NAME" -t -A \
    -c "SELECT 1 FROM schema_migrations WHERE filename = '$name'")
  if [ "$already_applied" = "1" ]; then
    echo "Skipping $name (already applied)"
    continue
  fi

  echo "Applying $name..."
  docker exec -i postgres psql -U "$DB_USER" -h "$DB_HOST" -d "$DB_NAME" -v ON_ERROR_STOP=1 < "$f"

  docker exec -i postgres psql -U "$DB_USER" -h "$DB_HOST" -d "$DB_NAME" -v ON_ERROR_STOP=1 \
    -c "INSERT INTO schema_migrations (filename) VALUES ('$name') ON CONFLICT DO NOTHING"
done

echo "All migrations applied."
