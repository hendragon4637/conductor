#!/usr/bin/env bash
# boot-verify gate for design-layout-v1
set -euo pipefail

echo "[gate] DESIGN.md token conformance"
# Ensure only recognised token markers are used (no raw project/package names)
if grep -En '(^|[^_])project-name([^_]|$)' DESIGN.md; then
  echo "FAIL: DESIGN.md contains raw 'project-name' instead of __PROJECT__"
  exit 1
fi

echo "[gate] exports directory exists and non-empty (when artifacts present)"
if ls exports/*.html 2>/dev/null; then
  find exports -type f -size +1k | grep . || {
    echo "FAIL: exports/ has .html files but all are empty (< 1k)"
    exit 1
  }
fi
