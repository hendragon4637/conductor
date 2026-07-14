#!/usr/bin/env bash
# 03_ratify_and_monitor.sh <plan_id>
# Ratifies a plan, then monitors the run until completion or failure.
# Output: Summary line with run state and node results.
set -euo pipefail

PLAN_ID="${1:?Usage: $0 <plan_id>}"
PLANNER_URL="${PLANNER_URL:-http://127.0.0.1:8093}"
POLL_INTERVAL="${POLL_INTERVAL:-60}"
MAX_POLLS="${MAX_POLLS:-60}"

echo "  Ratifying $PLAN_ID..."
RATIFY=$(curl -sfm 300 -X POST "$PLANNER_URL/ratify/$PLAN_ID" \
  -H 'Content-Type: application/json' 2>&1)

RATIFY_STATUS=$(echo "$RATIFY" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null)
RUN_ID=$(echo "$RATIFY" | python3 -c "import sys,json; print(json.load(sys.stdin).get('run_id',''))" 2>/dev/null)

if [ "$RATIFY_STATUS" != "ratified" ]; then
  echo "FAIL|$PLAN_ID|ratify_gate_failed|$(echo "$RATIFY" | python3 -c "import sys,json; print(json.load(sys.stdin).get('gate_feedback',''))" 2>/dev/null)"
  exit 0
fi

echo "  Run: $RUN_ID"

# Monitor loop
POLL=0
while [ $POLL -lt $MAX_POLLS ]; do
  sleep "$POLL_INTERVAL"
  POLL=$((POLL + 1))

  PENDING=$(docker exec postgres psql -U aipc -d aipc_conductor -tA \
    -c "SELECT count(*) FROM node_sessions WHERE run_id='$RUN_ID' AND gate_outcome IS NULL;" 2>/dev/null || echo "0")

  DONE=$(docker exec postgres psql -U aipc -d aipc_conductor -tA \
    -c "SELECT count(*) FROM node_sessions WHERE run_id='$RUN_ID' AND gate_outcome='done';" 2>/dev/null || echo "0")

  FAIL=$(docker exec postgres psql -U aipc -d aipc_conductor -tA \
    -c "SELECT count(*) FROM node_sessions WHERE run_id='$RUN_ID' AND gate_outcome='failed';" 2>/dev/null || echo "0")

  TOTAL=$(docker exec postgres psql -U aipc -d aipc_conductor -tA \
    -c "SELECT count(*) FROM node_sessions WHERE run_id='$RUN_ID';" 2>/dev/null || echo "0")

  echo "  [$POLL] pending=$PENDING done=$DONE failed=$FAIL total=$TOTAL"

  if [ "$PENDING" -eq 0 ] && [ "$TOTAL" -gt 0 ]; then
    echo "COMPLETE|$RUN_ID|done=$DONE failed=$FAIL"
    docker exec postgres psql -U aipc -d aipc_conductor -tA \
      -c "SELECT node_id || ' v=' || COALESCE(verdict,'?') || ' g=' || COALESCE(gate_outcome,'?') || ' l2=' || COALESCE(l2_score::text,'?') || ' a=' || attempt FROM node_sessions WHERE run_id='$RUN_ID' ORDER BY node_id, attempt;" 2>/dev/null
    exit 0
  fi
done

echo "TIMEOUT|$RUN_ID|pending=$PENDING done=$DONE failed=$FAIL"
