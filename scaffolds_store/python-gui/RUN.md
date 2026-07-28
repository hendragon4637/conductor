# Project

## Setup
uv venv .venv && source .venv/bin/activate && uv pip install -e ".[dev]"

## Run
.venv/bin/python -m app.main

## Test
.venv/bin/pytest -q

## Verify
.venv/bin/ruff check src tests
.venv/bin/pyinstaller --noconfirm app.spec
xvfb-run -a dist/app/app --smoke

## USE
dist/app/app — standalone executable (no env setup needed)
