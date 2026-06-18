#!/usr/bin/env bash
# Start Conductor Web UI on port 3090
set -e
cd "$(dirname "$0")/.."
export WORKSPACE_ROOT="${WORKSPACE_ROOT:-$(pwd)/workspace}"
.venv/bin/uvicorn backend.web.app:app --host 0.0.0.0 --port 3090 --log-level warning
