#!/usr/bin/env bash
# run_single_goal.sh — Submit, clarify, ratify, and monitor a single stress goal.
# Usage: ./run_single_goal.sh <goal_id> <project_id>
# Example: ./run_single_goal.sh sg-3e82f88b18b4 hetmoat-search-bar
#
set -euo pipefail

GOAL_ID="${1:?Usage: $0 <goal_id> <project_id>}"
PROJECT_ID="${2:?Usage: $0 <goal_id> <project_id>}"
PLANNER_URL="${PLANNER_URL:-http://127.0.0.1:8093}"
POLL_INTERVAL="${POLL_INTERVAL:-60}"  # seconds between run-status checks
MAX_POLLS="${MAX_POLLS:-60}"          # max 60 minutes

echo "=== [$(date +%H:%M:%S)] Starting: $PROJECT_ID ($GOAL_ID) ==="

# ── 1. Fetch goal spec from DB ──────────────────────────────────────────────
read -r TITLE SPEC <<< $(
  docker exec postgres psql -U aipc -d aipc_conductor -tA \
    -c "SELECT title, spec FROM stress_goals WHERE id='$GOAL_ID';"
)
if [ -z "$TITLE" ]; then
  echo "ERROR: Goal $GOAL_ID not found in stress_goals"
  exit 1
fi
echo "  Goal: $TITLE"

# ── 2. Submit to planner ────────────────────────────────────────────────────
echo "  Submitting to planner..."
SUBMIT=$(curl -sfm 30 -X POST "$PLANNER_URL/goal" \
  -H 'Content-Type: application/json' \
  -d "$(cat <<EOJSON
{
  "raw_input": "$TITLE",
  "spec": $(echo "$SPEC" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read().strip()))"),
  "project_id": "$PROJECT_ID",
  "origin": "human"
}
EOJSON
)" 2>&1) || { echo "SUBMIT FAILED: $SUBMIT"; exit 1; }

STATUS=$(echo "$SUBMIT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null)
PLAN_ID=$(echo "$SUBMIT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('plan_id',''))" 2>/dev/null)
echo "  Plan: $PLAN_ID (status=$STATUS)"

# ── 3. Clarify loop ─────────────────────────────────────────────────────────
MAX_CLARIFY=5
CLARIFY_ROUND=0
while [ "$STATUS" = "awaiting_clarification" ] && [ $CLARIFY_ROUND -lt $MAX_CLARIFY ]; do
  CLARIFY_ROUND=$((CLARIFY_ROUND + 1))
  echo "  Clarification round $CLARIFY_ROUND..."
  ANSWER=$(echo "$SPEC" | head -c 2000)
  CLARIFY=$(curl -sfm 30 -X POST "$PLANNER_URL/clarify/$PLAN_ID" \
    -H 'Content-Type: application/json' \
    -d "$(cat <<EOJSON
{"answer": $(echo "$ANSWER" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read()))")}
EOJSON
)" 2>&1) || { echo "CLARIFY FAILED: $CLARIFY"; exit 1; }
  STATUS=$(echo "$CLARIFY" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null)
  echo "  After clarify: status=$STATUS"
done

if [ "$STATUS" = "awaiting_clarification" ]; then
  echo "ERROR: Max clarify rounds ($MAX_CLARIFY) exhausted for $PROJECT_ID"
  exit 1
fi

# ── 4. Ratify ───────────────────────────────────────────────────────────────
echo "  Ratifying..."
RATIFY=$(curl -sfm 300 -X POST "$PLANNER_URL/ratify/$PLAN_ID" \
  -H 'Content-Type: application/json' 2>&1) || { echo "RATIFY FAILED: $RATIFY"; exit 1; }

RATIFY_STATUS=$(echo "$RATIFY" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null)
RUN_ID=$(echo "$RATIFY" | python3 -c "import sys,json; print(json.load(sys.stdin).get('run_id',''))" 2>/dev/null)
SCORE=$(echo "$RATIFY" | python3 -c "import sys,json; print(json.load(sys.stdin).get('plan_goal_review',''))" 2>/dev/null)
echo "  Ratify: status=$RATIFY_STATUS run_id=$RUN_ID score=$SCORE"

if [ "$RATIFY_STATUS" != "ratified" ]; then
  echo "ERROR: Ratification failed for $PROJECT_ID"
  echo "  Feedback: $(echo "$RATIFY" | python3 -c "import sys,json; print(json.load(sys.stdin).get('gate_feedback',''))" 2>/dev/null)"
  echo "  Response: $RATIFY"
  exit 1
fi

# ── 5. Monitor run ──────────────────────────────────────────────────────────
echo "  Monitoring run $RUN_ID..."
POLL=0
while [ $POLL -lt $MAX_POLLS ]; do
  sleep "$POLL_INTERVAL"
  POLL=$((POLL + 1))

  # Check node_sessions for this run
  NODES=$(docker exec postgres psql -U aipc -d aipc_conductor -tA \
    -c "SELECT count(*) FROM node_sessions WHERE run_id='$RUN_ID' AND gate_outcome IS NULL;" 2>/dev/null)
  PENDING="${NODES:-0}"

  DONE=$(docker exec postgres psql -U aipc -d aipc_conductor -tA \
    -c "SELECT count(*) FROM node_sessions WHERE run_id='$RUN_ID' AND gate_outcome='done';" 2>/dev/null)
  DONE="${DONE:-0}"

  FAIL=$(docker exec postgres psql -U aipc -d aipc_conductor -tA \
    -c "SELECT count(*) FROM node_sessions WHERE run_id='$RUN_ID' AND gate_outcome='failed';" 2>/dev/null)
  FAIL="${FAIL:-0}"

  TOTAL=$(docker exec postgres psql -U aipc -d aipc_conductor -tA \
    -c "SELECT count(*) FROM node_sessions WHERE run_id='$RUN_ID';" 2>/dev/null)
  TOTAL="${TOTAL:-0}"

  echo "  [$POLL] pending=$PENDING done=$DONE failed=$FAIL total=$TOTAL"

  # Check run overall state
  RUN_STATE=$(docker exec postgres psql -U aipc -d aipc_conductor -tA \
    -c "SELECT state FROM runs WHERE id='$RUN_ID';" 2>/dev/null)
  echo "  Run state: $RUN_STATE"

  # If no pending nodes and total > 0, we're done
  if [ "$PENDING" -eq 0 ] && [ "$TOTAL" -gt 0 ]; then
    echo "=== [$(date +%H:%M:%S)] Completed: $PROJECT_ID ==="
    echo "  done=$DONE failed=$FAIL total=$TOTAL"

    # Print per-node results
    docker exec postgres psql -U aipc -d aipc_conductor -tA \
      -c "SELECT node_id || ' verdict=' || COALESCE(verdict,'?') || ' gate=' || COALESCE(gate_outcome,'?') || ' l2=' || COALESCE(l2_score::text,'?') || ' attempt=' || attempt FROM node_sessions WHERE run_id='$RUN_ID' ORDER BY node_id, attempt;" 2>/dev/null

    break
  fi
done

if [ "$PENDING" -gt 0 ] && [ $POLL -ge $MAX_POLLS ]; then
  echo "WARNING: Timed out monitoring $PROJECT_ID ($RUN_ID) after $MAX_POLLS polls"
  exit 1
fi

echo ""
