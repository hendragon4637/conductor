#!/usr/bin/env bash
# boot-verify gate for python-gui-v1
set -euo pipefail

echo "[gate] pytest"
uv run pytest -q

echo "[gate] ruff check src tests"
uv run ruff check src tests

echo "[gate] pyinstaller build"
uv run pyinstaller --noconfirm app.spec

echo "[gate] smoke test (xvfb)"
xvfb-run -a "dist/__APP__/__APP__" --smoke
