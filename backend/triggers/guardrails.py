from __future__ import annotations

import os
from datetime import date, datetime
from functools import wraps
from typing import Any, Callable

_DAILY_LIMIT = int(os.environ.get("RATCHET_BUDGET_TOKENS", "50000"))


def _today_token_estimate() -> int:
    """Rough estimate of tokens consumed today by cron jobs.

    Reads a local counter file to approximate spend.
    """
    counter_file = "/tmp/conductor_daily_tokens"
    try:
        if os.path.isfile(counter_file):
            raw = open(counter_file).read().strip()
            parts = raw.split(" ", 1)
            if len(parts) == 2 and parts[0] == str(date.today()):
                return int(parts[1])
    except (OSError, ValueError):
        pass
    return 0


def _add_tokens(count: int) -> None:
    counter_file = "/tmp/conductor_daily_tokens"
    try:
        current = _today_token_estimate()
        with open(counter_file, "w") as f:
            f.write(f"{date.today()} {current + count}")
    except OSError:
        pass


def _under_daily_budget(estimated_cost: int = 5000) -> bool:
    """Check if we're under the daily token budget."""
    used = _today_token_estimate()
    return (used + estimated_cost) <= _DAILY_LIMIT


def with_guardrails(
    job_fn: Callable[[dict[str, Any]], dict[str, Any]],
    trigger: dict[str, Any],
) -> dict[str, Any]:
    """Wrap a job execution with budget, sandbox, and approval checks.

    Hard rules:
    1. Daily budget cap — reject if over.
    2. Sandboxed triggers force experiment worktrees.
    3. A mutation may auto-apply only to non-global scope.
       Global-scope mutations queue for human approval.
    """
    estimated_cost = 5000
    if not _under_daily_budget(estimated_cost):
        return {
            "status": "budget_exceeded",
            "message": f"Daily budget {_DAILY_LIMIT} exhausted",
            "token_estimate": _today_token_estimate(),
        }

    payload = trigger.get("payload", {})
    if trigger.get("sandboxed", True):
        payload["_sandboxed"] = True
        payload["_enforce_experiment_worktree"] = True

    result = job_fn(payload)

    _add_tokens(estimated_cost)

    if result.get("status") == "ok" and result.get("applied"):
        scope = payload.get("scope", "global")
        if scope == "global":
            result["_needs_human_approval"] = True
            result["_auto_applied"] = False
            result["message"] = (
                "Mutation queued for human approval — "
                "global scope requires manual confirm"
            )

    return result
