#!/usr/bin/env python3
"""E2E test runner — executes scenarios A–D and produces RESULTS.md."""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from dotenv import load_dotenv
load_dotenv("/opt/aipc/conductor/.env")

from tests.e2e.scenario_a import run as run_a
from tests.e2e.scenario_b import run as run_b
from tests.e2e.scenario_c import run as run_c
from tests.e2e.scenario_d import run as run_d

RESULTS_FILE = Path(__file__).parent / "RESULTS.md"
ALL_RESULTS: list[dict] = []
PER_SCENARIO: dict[str, tuple[int, int]] = {}


def run_and_collect(letter: str, fn) -> None:
    from tests.e2e import common
    common._RESULTS.clear()
    common._pass_count = 0
    common._fail_count = 0

    try:
        fn()
    except Exception as e:
        print(f"\n  !! Scenario {letter} raised: {e}")
        import traceback
        traceback.print_exc()

    for r in common._RESULTS:
        r["scenario"] = letter
        ALL_RESULTS.append(r)
    PER_SCENARIO[letter] = (common._pass_count, common._fail_count)


def write_results() -> None:
    total_pass = sum(1 for r in ALL_RESULTS if r["status"] == "PASS")
    total_fail = sum(1 for r in ALL_RESULTS if r["status"] == "FAIL")

    rows = [
        "| Scenario | Check | Status | Evidence |",
        "|----------|-------|--------|----------|",
    ]
    for r in ALL_RESULTS:
        rows.append(
            f"| {r.get('scenario','')} | {r['check']} | {r['status']} "
            f"| {r.get('evidence','')} |"
        )

    summary_lines = []
    for letter in sorted(PER_SCENARIO):
        p, f = PER_SCENARIO[letter]
        summary_lines.append(f"- **Scenario {letter}:** {p} passed, {f} failed")

    gate_lines = []
    for letter in sorted(PER_SCENARIO):
        _, f = PER_SCENARIO[letter]
        gate_lines.append(
            f"- Scenario {letter}: {'PASS' if f == 0 else 'FAIL'}"
        )

    md = f"""# E2E Scenario Test Results

{'|'.join(rows[0].split('|'))}
{'|'.join(rows[1].split('|'))}
""" + "\n".join(rows[2:]) + f"""

## Summary

- **Total checks:** {len(ALL_RESULTS)}
- **Passed:** {total_pass}
- **Failed:** {total_fail}
- **Timestamp:** {time.strftime('%Y-%m-%dT%H:%M:%S')}

### Per-Scenario
{chr(10).join(summary_lines)}

### GATE 13 Pass Condition
{chr(10).join(gate_lines)}
"""

    RESULTS_FILE.write_text(md)
    print(f"\nResults written to {RESULTS_FILE}")


def main():
    print("=" * 60)
    print("Conductor v4 — E2E Scenario Tests")
    print("=" * 60)

    scenarios = [
        ("A", "Single-agent code task", run_a),
        ("B", "Team task with two-level review", run_b),
        ("C", "Multimodal / VLM graceful-skip", run_c),
        ("D", "Ratchet via trigger", run_d),
    ]

    for letter, name, fn in scenarios:
        print(f"\n{'#' * 60}")
        print(f"# Scenario {letter}: {name}")
        print(f"{'#' * 60}")
        run_and_collect(letter, fn)

    write_results()

    total_fail = sum(f for _, f in PER_SCENARIO.values())
    if total_fail > 0:
        print(f"\n  {total_fail} check(s) failed across all scenarios")
        sys.exit(1)
    else:
        print("\n  All scenarios passed!")
        sys.exit(0)


if __name__ == "__main__":
    main()
