#!/usr/bin/env bash
# boot-verify gate for arduino-v1
set -euo pipefail

echo "[gate] pio compile (uno)"
pio run -e uno

echo "[gate] pio native test"
pio test -e native
