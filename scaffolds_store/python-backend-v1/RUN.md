# __APP__

## SETUP
uv venv .venv && uv pip install -e ".[dev]"

## RUN
uv run uvicorn __PKG__.main:app --reload

## TEST
uv run pytest -q

## Verify
bash gates.sh
