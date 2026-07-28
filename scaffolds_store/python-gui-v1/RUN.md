# __APP__

## SETUP
uv venv .venv && uv pip install -e ".[dev]"

## RUN
uv run python -m __PKG__.main

## TEST
uv run pytest -q

## Verify
bash gates.sh

## USE
dist/__APP__/__APP__ — standalone executable (no env setup needed)
