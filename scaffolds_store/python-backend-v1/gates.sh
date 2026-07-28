#!/usr/bin/env bash
# boot-verify gate for python-backend-v1
set -euo pipefail

echo "[gate] ruff check src tests"
uv run ruff check src tests

echo "[gate] pytest"
uv run pytest -q
