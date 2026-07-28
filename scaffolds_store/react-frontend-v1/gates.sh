#!/usr/bin/env bash
# boot-verify gate for react-frontend-v1
set -euo pipefail

echo "[gate] eslint src"
npm run lint

echo "[gate] vitest"
npm test -- --run

echo "[gate] tsc + vite build"
npm run build
