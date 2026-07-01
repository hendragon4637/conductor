#!/bin/bash
set -euo pipefail
set -a
source /opt/aipc/scripts/load-secrets.sh >/dev/null 2>&1
source /opt/aipc/conductor/.env >/dev/null 2>&1
set +a

cd /opt/aipc/conductor
exec setsid uv run uvicorn backend.main:app \
  --host 127.0.0.1 --port 8090 \
  > /tmp/conductor-backend.log 2>&1
