#!/usr/bin/env bash
# boot-verify gate for cli-tool-v1
set -euo pipefail

echo "[gate] ruff check src tests"
.venv/bin/ruff check src tests

echo "[gate] pytest"
.venv/bin/pytest -q

echo "[gate] help works"
.venv/bin/__PKG__ --help >/dev/null

echo "[gate] usage error must NOT exit 0"
if .venv/bin/__PKG__ --definitely-not-a-flag >/dev/null 2>&1; then
    echo "FAIL: bad args exited 0" >&2
    exit 1
fi
