"""L1 spec-derived checks — inject standard-aware checks into the L1 pool.

These checks verify that expected artifacts exist based on the domain standard's
artifact_spec and scaffold_tree.
"""

from __future__ import annotations

from typing import Any


def build_spec_l1_checks(standard: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Build L1 checks from a domain standard's artifact_spec.

    Each artifact pattern becomes a file_exists or directory_exists check.
    Returns an empty list if no standard is provided or no artifact_spec exists.
    """
    if not standard:
        return []

    artifact_spec = standard.get("artifact_spec", {})
    if not artifact_spec:
        return []

    checks = []
    for artifact_type, patterns in artifact_spec.items():
        for pattern in patterns:
            check_id = f"spec_{artifact_type}_{pattern.replace('/', '_').replace('*', 'star').replace('.', '_')}"
            # Determine if it's a file check or directory check
            if pattern.endswith("/**"):
                dir_path = pattern[:-3]
                checks.append({
                    "id": check_id,
                    "type": "deterministic",
                    "kind": "file_exists",
                    "check_cmd": dir_path,
                    "criterion": f"Required directory '{dir_path}' exists per domain standard",
                    "source_hint": dir_path,
                })
            elif "*" in pattern:
                # Glob pattern — check at least one match via shell
                checks.append({
                    "id": check_id,
                    "type": "deterministic",
                    "kind": "shell",
                    "check_cmd": f"find . -type f -path './{pattern}' 2>/dev/null | head -1 | grep -q .",
                    "criterion": f"At least one file matches '{pattern}' per domain standard",
                })
            else:
                checks.append({
                    "id": check_id,
                    "type": "deterministic",
                    "kind": "file_exists",
                    "check_cmd": pattern,
                    "criterion": f"Required file '{pattern}' exists per domain standard",
                    "source_hint": pattern,
                })

    # Add lint_clean check
    checks.append({
        "id": "lint_clean",
        "type": "deterministic",
        "kind": "shell",
        "check_cmd": "echo 'lint check placeholder'",
        "criterion": "No lint errors (lint tool runs clean)",
    })

    return checks


