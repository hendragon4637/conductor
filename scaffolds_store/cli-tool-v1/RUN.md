# __APP__

## SETUP
uv venv .venv && uv pip install -e .

## RUN
uv run __PKG__ shout "hello world"

## TEST
uv run pytest -q

## VERIFY
bash gates.sh

## USE
```
uv run __PKG__ shout "hello world"
# HELLO WORLD

uv run __PKG__ --version
# __PKG__ 0.1.0
```
