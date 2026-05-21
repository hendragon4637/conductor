"""
Deterministic eval — runs the golden task's success_criteria as commands.

Week 1: minimal heuristic — checks:
  - Receipt status == 'completed' or 'partial'
  - All tests in test_results passed
  - No commands_run with non-zero exit_code

Future: load `tNN.md` from golden/ and parse the criteria block into actual
runnable scripts (e.g. pytest path, python -m py_compile, custom shell).
"""
from __future__ import annotations
from typing import Any


def score_deterministic(trace: dict) -> dict:
    """Compute one composite deterministic score from the trace's output_spec."""
    output = trace.get("output_spec") or {}
    clauses_violated: list[str] = []
    score = 1.0

    # Receipt status check
    receipt_status = output.get("status")
    if receipt_status == "failed":
        score = 0.0
        clauses_violated.append("receipt_failed")
    elif receipt_status == "partial":
        score = 0.5
        clauses_violated.append("receipt_partial")
    elif receipt_status not in ("completed",):
        score = 0.3
        clauses_violated.append("receipt_unknown_status")

    # Test results
    tr = output.get("test_results") or {}
    failed = tr.get("failed") or 0
    passed = tr.get("passed") or 0
    if failed > 0:
        score *= 0.5
        clauses_violated.append(f"tests_failed:{failed}")
    elif passed == 0 and tr:
        score *= 0.7
        clauses_violated.append("tests_present_but_none_passed")
    elif not tr:
        score *= 0.8
        clauses_violated.append("no_test_results_reported")

    # Commands run with bad exit codes
    cmds = output.get("commands_run") or []
    bad_exits = [c for c in cmds if c.get("exit_code") not in (0, None)]
    if bad_exits:
        score *= 0.7
        clauses_violated.append(f"nonzero_exits:{len(bad_exits)}")

    # Clauses skipped — penalty
    skipped = output.get("clauses_skipped") or []
    if skipped:
        score *= 0.9 ** len(skipped)
        for s in skipped:
            cid = s.get("clause_id", "?")
            clauses_violated.append(f"skipped:{cid}")

    score = max(0.0, min(1.0, score))

    return {
        "track": "deterministic",
        "dimension": "composite",
        "value": round(score, 4),
        "clauses_violated": clauses_violated,
    }
