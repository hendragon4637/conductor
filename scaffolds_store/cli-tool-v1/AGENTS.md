# AGENTS.md — Python CLI Tool Standard (v1)

## Layout
```
pyproject.toml         # [project.scripts] __PKG__ = "__PKG__.cli:main"  ← installs the command
src/__PKG__/
  cli.py               # argparse/typer wiring ONLY: parse args → call core → format output → exit code
  core/                # the actual logic. NO argparse, NO print, NO sys.exit → unit-testable
  __init__.py
tests/                 # test_core.py (pure) + test_cli.py (invoke via subprocess or CliRunner)
RUN.md                 # incl. USE: install + example invocations
```
The cli.py / core split is the rule everything else depends on: logic must be callable and testable
without simulating a terminal.

## Interface rules [consensus — CLI design conventions]
- `--help` works on the root command and EVERY subcommand, exits 0, and lists usage + options.
- `--version` prints the version and exits 0.
- Exit codes: `0` success, `1` runtime failure, `2` usage error (bad args). Never exit 0 on failure.
- Errors go to **stderr**, results to **stdout** — so output is pipeable.
```python
# GOOD  cli.py
def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        print(core.convert(args.input))      # result → stdout
        return 0
    except UserError as e:
        print(f"error: {e}", file=sys.stderr)  # message → stderr
        return 1
```
- Reads stdin when the input argument is `-` (pipe-friendly). Add `--json` for machine-readable output
  whenever results are structured.
- No interactive prompts unless `--interactive` is passed explicitly — the tool must be scriptable.
- Long operations print progress to stderr, never to stdout.

## Style rules
- Full type hints; ruff clean.
- `core/` raises typed exceptions (`UserError`, `DataError`); `cli.py` translates them into exit codes.
  Never let a raw traceback reach the user for an expected error condition.
- Config precedence, when a config file exists: CLI flag > env var > config file > default. Document it.

## Testing
- `core/`: direct unit tests, no CLI involved. This is where most coverage lives.
- `cli.py`: invoke `main([...])` directly and assert the return code + captured stdout/stderr.
- Required cases: `--help` exits 0; a valid invocation exits 0 with expected stdout; an invalid argument
  exits 2 with a message on stderr.
- `pytest -q` must exit 0 before completion.

## Delivery
- `pip install -e .` puts `__PKG__` on PATH; `__PKG__ --help` works from any directory.
- RUN.md USE section shows at least two real invocations with their expected output.

## Process
- Run `bash gates.sh` before reporting completion. Update RUN.md when flags/behavior change.
- No scope expansion; don't add subcommands the task didn't ask for.

---
**Provenance:** stdout/stderr separation, exit-code conventions (0/1/2), --help/--version, stdin `-`,
non-interactive-by-default, config precedence = [consensus] long-standing CLI/POSIX practice.
cli/core split as a hard rule + the three required test cases = [synthesis] (rationale: makes logic
testable without a terminal and gives L1 concrete, cheap deterministic checks).
