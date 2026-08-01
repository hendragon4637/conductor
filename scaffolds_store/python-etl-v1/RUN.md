# __APP__

## SETUP
uv venv .venv && uv pip install -e ".[dev]"

## RUN
uv run python -m __PKG__.pipeline --input data/input/sample.csv --output data/output/out.csv

## TEST
uv run pytest -q

## VERIFY
bash gates.sh
