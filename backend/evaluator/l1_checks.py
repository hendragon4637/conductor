"""L1 deterministic checks — run shell commands in the node worktree.

Each deterministic check is a shell command. Exit 0 = pass, anything else = fail.
If a node has no deterministic checks, L1 passes vacuously.
"""
from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass, field


@dataclass
class L1Result:
    """Result of running all deterministic checks for a node."""
    passed: bool
    detail: list[tuple[str, bool, str]] = field(default_factory=list)
    """(check_id, ok, output_tail) for each deterministic check run."""
    duration_s: float = 0.0
    """Total wall-clock time to run all checks."""


def run_l1(
    checks: list,
    worktree: str,
    timeout: int = 300,
) -> L1Result:
    """Run all deterministic checks in the node's worktree.

    Args:
        checks: List of ``Check`` objects from the node (only ``type=="deterministic"``
                with a non-None ``check_cmd`` are executed).
        worktree: Absolute path to the node's git worktree.
        timeout: Per-check timeout in seconds.

    Returns:
        ``L1Result`` with passed=True only if ALL deterministic checks exit 0.
        Nodes with no deterministic checks pass vacuously.
    """
    start = time.time()
    detail: list[tuple[str, bool, str]] = []

    for c in checks:
        if getattr(c, "tier", None) != "L1":
            continue
        cmd = getattr(c, "check_cmd", None)
        if not cmd:
            continue

        try:
            r = subprocess.run(
                cmd,
                cwd=worktree,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            ok = r.returncode == 0
            tail = (r.stdout + r.stderr)[-500:]
            detail.append((c.id, ok, tail))
        except subprocess.TimeoutExpired:
            detail.append((c.id, False, "timeout"))
        except FileNotFoundError:
            detail.append((c.id, False, "command not found"))
        except Exception as e:
            detail.append((c.id, False, f"error: {e}"))

    passed = all(ok for _, ok, _ in detail) if detail else True
    duration = time.time() - start
    return L1Result(passed=passed, detail=detail, duration_s=duration)
