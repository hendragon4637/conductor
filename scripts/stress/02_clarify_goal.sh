#!/usr/bin/env bash
# 02_clarify_goal.sh <plan_id> "<answer>"
# Sends a clarification answer to the planner.
# Output: <status>|<plan_id> OR <status>|<plan_id>|<questions> (if still pending)
set -euo pipefail

PLAN_ID="${1:?Usage: $0 <plan_id> \"<answer>\"}"
ANSWER="${2:?Usage: $0 <plan_id> \"<answer>\"}"
PLANNER_URL="${PLANNER_URL:-http://127.0.0.1:8093}"

python3 -c "
import json
print(json.dumps({'answer': '''$ANSWER'''}))
" > /tmp/_clarify_payload.json

RESP=$(curl -sfm 30 -X POST "$PLANNER_URL/clarify/$PLAN_ID" \
  -H 'Content-Type: application/json' \
  -d @/tmp/_clarify_payload.json 2>&1)

STATUS=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null)

if [ "$STATUS" = "awaiting_clarification" ]; then
  QUESTIONS=$(echo "$RESP" | python3 -c "
import sys,json
d = json.load(sys.stdin)
qs = d.get('questions', d.get('reason', 'no details'))
print(qs)
" 2>/dev/null)
  echo "pending|$PLAN_ID|$QUESTIONS"
else
  echo "ready|$PLAN_ID"
fi
