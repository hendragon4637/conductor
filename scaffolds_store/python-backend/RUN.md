# Project

## Setup
uv venv .venv && source .venv/bin/activate && uv pip install -e ".[dev]"

## Run
uv run uvicorn app.main:app --reload

## Test
.venv/bin/pytest -q

## Verify
.venv/bin/ruff check src tests
