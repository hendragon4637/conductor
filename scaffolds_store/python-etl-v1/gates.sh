#!/usr/bin/env bash
# boot-verify gate for python-etl-v1
set -euo pipefail

echo "[gate] ruff check src tests"
.venv/bin/ruff check src tests

echo "[gate] pytest"
.venv/bin/pytest -q

echo "[gate] pipeline runs"
.venv/bin/python -m __PKG__.pipeline --input data/input/sample.csv --output data/output/out.csv

echo "[gate] output produced"
test -s data/output/out.csv

echo "[gate] idempotent re-run"
cp data/output/out.csv /tmp/etl-out-1.csv
.venv/bin/python -m __PKG__.pipeline --input data/input/sample.csv --output data/output/out.csv
diff -q /tmp/etl-out-1.csv data/output/out.csv
