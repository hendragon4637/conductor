#!/bin/bash
set -euo pipefail
set -a
source /opt/aipc/scripts/load-secrets.sh >/dev/null 2>&1
source /opt/aipc/conductor/.env >/dev/null 2>&1
set +a

cd /opt/aipc/conductor
exec uv run python backend/tests/2026-06-26/e2e_h1_full.py
