#!/usr/bin/env bash
# boot-verify gate for design-layout-v2 (variant-aware).
# Runs in the generated worktree AFTER seeding: tokens.css + reference.html
# have been copied from the pinned variant and work/ holds the component.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "[gate] design manifest + variant pin"
VARIANT=""
if [ -f "$ROOT/.conductor/workspace.json" ]; then
  VARIANT="$(python3 -c 'import json,sys
d=json.load(open(sys.argv[1]))
v=d.get("variant","")
if not v:
    v=(d.get("components") or [{}])[0].get("variant","")
print(v)' "$ROOT/.conductor/workspace.json")"
fi
if [ -n "$VARIANT" ]; then
  echo "[gate] variant pinned: $VARIANT"
else
  echo "[gate] no variant pin — verifying DESIGN.md <-> tokens.css consistency only"
fi

echo "[gate] DESIGN.md token conformance"
# Ensure only recognised token markers are used (no raw project/package names)
if grep -En '(^|[^_])project-name([^_]|$)' DESIGN.md; then
  echo "FAIL: DESIGN.md contains raw 'project-name' instead of __PROJECT__"
  exit 1
fi

echo "[gate] check_tokens.py conformance (tokens_used, no raw literals, off-scale px, AA contrast)"
if ! python3 "$ROOT/scripts/check_tokens.py" "$ROOT"; then
  echo "FAIL: check_tokens.py reported violations"
  exit 1
fi

echo "[gate] exports directory exists and non-empty (when artifacts present)"
if ls exports/*.html 2>/dev/null; then
  find exports -type f -size +1k | grep . || {
    echo "FAIL: exports/ has .html files but all are empty (< 1k)"
    exit 1
  }
fi

echo "ALL GATES GREEN"