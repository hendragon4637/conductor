#!/usr/bin/env bash
# p1_submit_all.sh — Submit all 10 stress goals sequentially.
# Total timeout: 3 hours (10800s), per-goal timeout: 10min (600s), 5min (300s) gap between goals.
set -euo pipefail

declare -A GOALS=(
  ["hetmoat-search-bar"]="sg-3e82f88b18b4"
  ["hetmoat-customer-api"]="sg-929ef95a7826"
  ["hetmoat-password-reset"]="sg-1a9588690568"
  ["hetmoat-login-spec"]="sg-8a61cfae22d6"
  ["hetmoat-notif-banner"]="sg-e2c6b39fa9b7"
  ["hetmoat-email-sig"]="sg-7bc0f93e4caa"
  ["hetmoat-newsletter-layout"]="sg-ae4d50b95ce2"
  ["hetmoat-product-desc"]="sg-1863a16ed83b"
  ["hetmoat-social-post"]="sg-9c588bc34fd7"
  ["hetmoat-banner-ad"]="sg-29ddefdd45bb"
)

TOTAL_TIMEOUT=10800   # 3 hours
GOAL_TIMEOUT=600      # 10 min per goal
SLEEP_BETWEEN=300     # 5 min between goals
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
START_TS=$(date +%s)
INDEX=0

for PROJECT_ID in "${!GOALS[@]}"; do
  GOAL_ID="${GOALS[$PROJECT_ID]}"
  INDEX=$((INDEX + 1))
  NOW=$(date +%s)
  ELAPSED=$((NOW - START_TS))

  if [ $ELAPSED -ge $TOTAL_TIMEOUT ]; then
    echo "[TIMEOUT] Total time exceeded at goal $INDEX/$PROJECT_ID"
    exit 1
  fi

  REMAINING=$((TOTAL_TIMEOUT - ELAPSED))
  ADJUSTED=$(( GOAL_TIMEOUT < REMAINING ? GOAL_TIMEOUT : REMAINING ))

  echo "[$(date +%H:%M:%S)] === $INDEX/10: $PROJECT_ID ($GOAL_ID) ==="

  RESP=$(timeout "$ADJUSTED" bash "$SCRIPT_DIR/01_submit_goal.sh" "$GOAL_ID" "$PROJECT_ID" 2>&1) || {
    echo "[FAIL] $PROJECT_ID submit timed out or errored: $RESP"
    continue
  }

  echo "  $RESP"

  if [ $INDEX -lt 10 ]; then
    echo "  Sleeping 5min before next goal..."
    sleep "$SLEEP_BETWEEN"
  fi
done

echo "[$(date +%H:%M:%S)] Phase 1 complete — all 10 goals submitted."
