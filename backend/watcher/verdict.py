"""Deterministic verdict logic — no LLM calls in this path."""
from __future__ import annotations

import time

from backend.observability.signals import compute_session_signals

# Verdict constants — avoid string typos
VERDICT_RUNNING = "running"
VERDICT_DONE = "done"
VERDICT_STALLED = "stalled"
VERDICT_FAILED = "failed"
VERDICT_QUOTA = "quota"
VERDICT_CRASHED = "crashed"


def verdict(
    state: dict,
    sig: dict | None = None,
) -> str:
    """Deterministic multi-signal verdict for a session.

    ANDs multiple weak signals to avoid false positives during legitimate
    long thinking or test runs.

    Args:
        state: Session state dict with keys ``pid``, ``thresholds``.
        sig: Signal snapshot dict from ``compute_session_signals``.
             If None, re-computes from events (expensive — pass pre-computed).

    Returns:
        One of ``"running" | "done" | "stalled" | "quota" | "crashed"``.
    """
    if sig is None:
        return VERDICT_RUNNING  # caller must supply signals

    pid_alive = sig.get("pid_alive", False)
    have_query_data = sig.get("have_query_data", False)
    terminal = sig.get("terminal", False)
    any_error = sig.get("any_error", False)
    quota_suspected = sig.get("quota_suspected", False)
    token_rate = sig.get("token_rate", 0.0)
    fs_changed = sig.get("fs_changed", False)
    last_activity = sig.get("last_activity", 0.0)
    age_s = sig.get("age_s")
    stall_s = state.get("thresholds", {}).get("stall_s", 120)

    # PID is unreliable for in-process watcher registrations (same process).
    # Only trust it when AionUi has NO data to go on (e.g. stale bootstrapped
    # sessions from before a backend restart).  If AionUi has conversation data,
    # it is the authoritative source on session health.
    if not pid_alive and not have_query_data:
        return VERDICT_CRASHED
    if any_error:
        return VERDICT_FAILED
    if terminal:
        return VERDICT_DONE
    if quota_suspected and token_rate == 0.0 and not fs_changed:
        return VERDICT_QUOTA
    age = age_s if age_s is not None else (time.time() - last_activity)
    if age > stall_s and token_rate == 0.0 and not fs_changed:
        return VERDICT_STALLED
    return VERDICT_RUNNING
