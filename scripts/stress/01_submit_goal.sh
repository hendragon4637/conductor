#!/usr/bin/env bash
# 01_submit_goal.sh <goal_id> <project_id>
# Submits a stress_goal to the planner.
# Output: <plan_id>|<status>
set -euo pipefail

GOAL_ID="${1:?Usage: $0 <goal_id> <project_id>}"
PROJECT_ID="${2:?Usage: $0 <goal_id> <project_id>}"
PLANNER_URL="${PLANNER_URL:-http://127.0.0.1:8093}"

read -r TITLE SPEC <<< $(
  docker exec postgres psql -U aipc -d aipc_conductor -tA \
    -c "SELECT title, spec FROM stress_goals WHERE id='$GOAL_ID';"
)

python3 -c "
import json, sys
spec = '''$SPEC'''
title = '''$TITLE'''
print(json.dumps({
    'raw_input': title.strip(),
    'spec': spec.strip(),
    'project_id': '$PROJECT_ID',
    'origin': 'human',
}))
" > /tmp/_goal_payload.json

RESP=$(curl -sfm 30 -X POST "$PLANNER_URL/goal" \
  -H 'Content-Type: application/json' \
  -d @/tmp/_goal_payload.json 2>&1)

PLAN_ID=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('plan_id',''))" 2>/dev/null)
STATUS=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null)
echo "$PLAN_ID|$STATUS"
