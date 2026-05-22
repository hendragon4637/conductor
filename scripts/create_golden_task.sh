#!/usr/bin/env bash
# Quick golden-task creator.
# Usage: ./create_golden_task.sh "your task intent here"
set -euo pipefail

PROJECT_ID="aipc-golden-eng"
SESSION_ID="golden/01"
INTENT="$*"

if [ -z "$INTENT" ]; then
  echo "Usage: $0 <intent>"
  exit 1
fi

JSON=$(printf '{"project_id":"%s","session_id":"%s","user_intent":%s}' \
  "$PROJECT_ID" "$SESSION_ID" "$(printf '%s' "$INTENT" | jq -Rs .)")

curl -sX POST http://127.0.0.1:8090/api/tasks \
  -H 'content-type: application/json' \
  -d "$JSON" | jq
