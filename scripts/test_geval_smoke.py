#!/usr/bin/env python3
"""Smoke test for deepeval GEval integration through LiteLLM gateway.

Runs a simple GEval metric against the JUDGE model and reports JSON validity.
If JSON errors occur, recommends schema confinement or judge model bump.

Usage:
    uv run python scripts/test_geval_smoke.py

Environment:
    LITELLM_BASE, LITELLM_KEY_EVALUATION must be set.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root))

from deepeval.metrics import GEval
from deepeval.test_case import LLMTestCase, SingleTurnParams

from shared.eval_models import JUDGE


def main() -> int:
    print("=== GEval Smoke Test ===")
    print(f"JUDGE model: {JUDGE.get_model_name()}")
    print(f"JUDGE base_url: {JUDGE.base_url}")
    print(f"JUDGE temperature: {JUDGE.temperature}")
    print()

    metric = GEval(
        name="smoke_test",
        criteria="Determine if the output contains a greeting",
        evaluation_params=[
            SingleTurnParams.INPUT,
            SingleTurnParams.ACTUAL_OUTPUT,
        ],
        model=JUDGE,
        threshold=0.5,
    )

    test_case = LLMTestCase(
        input="Say hello to the user",
        actual_output="Hello! Welcome to our service.",
    )

    print("Running GEval measure...")
    try:
        metric.measure(test_case)
        score = metric.score
        reason = metric.reason
        print(f"Score: {score}")
        print(f"Reason: {reason}")
        print("SUCCESS: GEval returned valid scores without JSON errors")
        return 0

    except Exception as e:
        error_str = str(e)
        print(f"ERROR: {error_str[:500]}")

        if "JSON" in error_str or "parse" in error_str.lower():
            print()
            print("JSON ERROR DETECTED — judge model likely returned unparseable JSON.")
            print("Recommended fixes (in priority order):")
            print("  1. Check LiteLLM gateway logs for the raw judge response")
            print("  2. Enable schema-confined generation per deepeval custom-LLM guide")
            print("     (pydantic confinement on the LiteLLMModel)")
            print("  3. Bump judge model to gpt-oss-120b (more reliable JSON output)")
        else:
            print()
            print("Non-JSON error — check LiteLLM gateway connectivity and credentials.")
            print(f"LITELLM_BASE={os.environ.get('LITELLM_BASE', '(not set)')}")
            print(f"LITELLM_KEY_EVALUATION={'set' if os.environ.get('LITELLM_KEY_EVALUATION') else '(not set)'}")

        return 1


if __name__ == "__main__":
    sys.exit(main())
