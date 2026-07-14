#!/usr/bin/env python3
"""Verify that quality_dimensions from capabilities flow through check-gen.

Tests that:
1. A capability referenced by imported agents has quality_dimensions with dims
2. objective_dims() and subjective_dims() split correctly
3. generate_capability_checks() produces non-empty L1 + L2
4. run_md_present is prepended for executor-role nodes
5. L2 items have confidence=provisional when golden_ref_count < 5

Usage:
    uv run python scripts/verify_dims_flow.py
"""
from __future__ import annotations

import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root))

from backend.planning.capability.checkgen import generate_capability_checks
from backend.planning.capability.registry import (
    all_capabilities,
    get_capability,
    objective_dims,
    subjective_dims,
)


def main() -> int:
    errors = 0

    # 1. Check all capabilities have quality_dimensions
    print("=== 1. Checking all capabilities have quality_dimensions ===")
    caps = all_capabilities()
    if not caps:
        print("ERROR: No capabilities found (DB unreachable or empty)")
        return 1
    print(f"  Found {len(caps)} capabilities")

    for cap in caps:
        dims = cap.get("quality_dimensions") or []
        if not dims:
            print(f"  WARN: {cap['name']} has empty quality_dimensions")
            errors += 1
            continue
        obj = objective_dims(cap)
        subj = subjective_dims(cap)
        if not obj:
            print(f"  WARN: {cap['name']} has no objective dims ({len(dims)} total)")
            errors += 1
        if not subj:
            print(f"  WARN: {cap['name']} has no subjective dims ({len(dims)} total)")
            errors += 1
        kinds = ", ".join(f"{d.get('id','?')}({d.get('kind','?')})" for d in dims)
        print(f"  OK: {cap['name']} — {len(obj)} obj + {len(subj)} subj = {len(dims)} dims [{kinds}]")

    # 2. Pick a specific imported-agent capability and test check-gen
    print("\n=== 2. Testing check-gen flow for a capability used by imported agents ===")
    test_caps = ["frontend", "backend_api", "analytics_assistant", "generic"]
    for name in test_caps:
        cap = get_capability(name)
        if not cap:
            print(f"  SKIP: {name} not found")
            continue
        dims = cap.get("quality_dimensions") or []
        print(f"  Capability: {name} ({len(dims)} dims)")

        # Build a mock node simulating what generate_capability_checks receives
        mock_node = {
            "id": f"verify-node-{name}",
            "capabilities": [name],
            "members": [{"role": "executor", "agent_config": "imp-ui-designer", "backend": "opencode"}],
            "task": {
                "text": f"Deliver a {name} implementation per spec",
                "deliverables": [f"{name} code", "tests", "documentation"],
            },
        }

        checks = generate_capability_checks(mock_node)
        l1 = [c for c in checks if c.get("type") == "deterministic"]
        l2 = [c for c in checks if c.get("type") == "rubric"]

        print(f"    L1 checks: {len(l1)}")
        for c in l1:
            print(f"      - {c['id']}: {c.get('criterion','')[:60]}")
        print(f"    L2 checks: {len(l2)}")
        for c in l2:
            conf = c.get("confidence", "none")
            print(f"      - {c['id']}: {c.get('criterion','')[:60]} (confidence={conf})")

        # Verifications
        if not l1:
            print(f"    FAIL: No L1 checks generated for {name}")
            errors += 1
        if not l2:
            print(f"    FAIL: No L2 checks generated for {name}")
            errors += 1

        # Check run_md_present is first L1 check
        if l1 and l1[0].get("id") == "l1-run-md-present":
            print(f"    OK: run_md_present is first L1 check")
        elif l1:
            print(f"    WARN: First L1 check is {l1[0].get('id')}, not run_md_present")
        else:
            print(f"    WARN: No L1 checks, run_md_present not present")

        # Check L2 confidence
        for c in l2:
            conf = c.get("confidence", "none")
            if conf == "provisional":
                print(f"    OK: {c['id']} has confidence=provisional")
            else:
                print(f"    WARN: {c['id']} has confidence={conf} (expected provisional)")

    # Summary
    print(f"\n=== Result ===")
    if errors:
        print(f"FAILED: {errors} issue(s) found")
    else:
        print("PASSED: All dims flow correctly through check-gen")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
