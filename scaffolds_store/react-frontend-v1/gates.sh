#!/usr/bin/env bash
# boot-verify gate for react-frontend-v1
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "[gate] eslint src"
npm run lint

echo "[gate] vitest"
npm test -- --run

echo "[gate] tsc + vite build"
npm run build

# Token conformance — only when the design handoff copied tokens + the gate
# script into this component (standalone frontends have neither and skip).
if [ -f "$ROOT/scripts/check_tokens.py" ]; then
  echo "[gate] token conformance (design handoff)"
  if ! python3 "$ROOT/scripts/check_tokens.py" "$ROOT"; then
    echo "FAIL: token conformance reported violations"
    exit 1
  fi
fi

echo "ALL GATES GREEN"
