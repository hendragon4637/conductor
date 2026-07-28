from __future__ import annotations

from services.intake.adapters.base import SourceAdapter

_ADAPTERS: dict[str, SourceAdapter] = {}


def register(adapter: SourceAdapter) -> None:
    """Register an adapter instance by its origin name."""
    _ADAPTERS[adapter.origin] = adapter


def adapter_for(origin: str) -> SourceAdapter:
    """Return the registered adapter for *origin* or raise."""
    if origin not in _ADAPTERS:
        raise ValueError(f"Unknown intake origin: {origin!r}")
    return _ADAPTERS[origin]


# ── Auto-register all built-in adapters ─────────────────────────────────

from services.intake.adapters.human_feedback import HumanFeedbackAdapter
from services.intake.adapters.l4_findings import L4FindingsAdapter
from services.intake.adapters.plan_failed import PlanFailedAdapter
from services.intake.adapters.ratify_rejected import RatifyRejectedAdapter
from services.intake.adapters.run_failed import RunFailedAdapter

for _a in (
    RunFailedAdapter(),
    L4FindingsAdapter(),
    PlanFailedAdapter(),
    RatifyRejectedAdapter(),
    HumanFeedbackAdapter(),
):
    register(_a)
