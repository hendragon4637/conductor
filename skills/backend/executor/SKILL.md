---
name: backend-executor
version: 0.0.1
description: Python/FastAPI backend executor (week-1 stub; will be ratcheted)
---

# Backend Executor — Initial Stub

This skill is intentionally minimal in week 1. It will be ratcheted based on
hand-labeled failure modes from the 30-task golden set.

## Hard rules
1. Use type hints on all public functions.
2. Tests required for every new endpoint.
3. Use httpx, not requests.
4. asyncio for I/O.

## Verification before declaring done
- `python -m py_compile <new_files>`
- `pytest -q` passes

## Provenance
Authored as initial stub. To be replaced by ratcheted version.
