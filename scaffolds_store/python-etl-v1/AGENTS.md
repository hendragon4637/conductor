# AGENTS.md — Python Data Pipeline Standard (v1)

## Scope [researched — deliberate choice]
Plain-Python ETL: **no orchestrator** (Airflow/Dagster/Prefect deploy infrastructure — not warranted for
single-pipeline tasks). Orchestration is a separate standard, chosen only when a goal needs scheduling,
retries across tasks, or lineage UI. Keep the pipeline importable and runnable as a plain module so an
orchestrator can wrap it later without rewrites.

## Layout [researched — ETL project-structure consensus]
```
pyproject.toml
src/__PKG__/
  extract/   # I/O IN  — one module per source (API, CSV, DB). Returns raw records.
  transform/ # PURE functions: data in → data out. NO I/O, NO network, NO file reads.
  load/      # I/O OUT — one module per sink
  quality/   # validation rules (schema, ranges, nulls, dupes)
  pipeline.py# orchestrates extract → transform → load; the only place they meet
  config.py  # paths/URIs/env — never hardcoded in modules
data/
  input/     # sample/fixture inputs (small, committed)
  output/    # generated artifacts (gitignored)
tests/       # test_transform.py (pure, no mocks needed), test_extract.py / test_load.py (mocked I/O)
RUN.md
```

## Style rules
- **Transforms are pure.** This is the rule everything else serves: pure transforms are testable with no
  mocks, no network, no DB. If a transform needs config, pass it as an argument. [researched — modular/testable ETL practice]
```python
# GOOD  src/__PKG__/transform/clean.py
def drop_invalid(rows: list[dict], min_qty: int = 1) -> list[dict]:
    return [r for r in rows if r["quantity"] >= min_qty and r["price"] > 0]
# BAD — untestable, hidden I/O
def drop_invalid():
    rows = pd.read_csv("sales.csv"); ...
```
- **Validate at the boundaries** (`quality/`): after extract and before load. Fail loudly with a summary
  (how many rows, which rule) — never silently drop bad data. [researched — data-validation practice]
- Idempotent runs: re-running with the same input produces the same output; writes are atomic
  (write temp → rename), never partial. [consensus]
- Type hints on all public functions; pandas optional — plain dicts/lists are fine and faster to test.
- Logging over prints: row counts per stage, rejects per rule, duration. A run must be auditable from logs.
- No secrets in code — config from env with documented defaults.

## Testing
- `transform/` and `quality/` : direct unit tests with literal fixtures, no mocks. This is where coverage lives.
- `extract/`/`load/`: mocked I/O (monkeypatch/unittest.mock) — never hit real networks or databases in tests.
- One end-to-end test: `data/input/sample.csv` → `pipeline.run()` → assert the output artifact's contents.
- `pytest -q` must exit 0 before completion.

## Delivery
- `python -m __PKG__.pipeline --input data/input/x.csv --output data/output/y.csv` produces the artifact.
- RUN.md documents inputs, outputs, and the exact command; the sample input must work as-is.

## Process
- Run `bash gates.sh` before reporting completion. Update RUN.md when inputs/outputs/commands change.
- No scope expansion; do not introduce an orchestrator unless the task explicitly asks.

---
**Provenance:** extract/transform/load/quality module split + data/input|output convention = [researched]
(ETL project-structure guides 2025-26). Pure-transform testability, boundary validation, mocked-I/O tests
= [researched] (modular/testable pipeline practice, ETL testing guides). No-orchestrator scoping decision,
idempotent atomic writes, logging contract = [synthesis] (rationale: keeps the domain verifiable on a
single host and defers Airflow to its own standard).
