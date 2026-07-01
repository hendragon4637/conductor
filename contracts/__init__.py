"""Shared event contracts for Conductor microservices.

All services import event payload models from here — the single source of truth
for message shapes across the RabbitMQ event bus.
"""

from contracts.events import (
    PlanRatified,
    NodeDispatch,
    NodeSpawned,
    NodeObserved,
    GateEvaluated,
    NodeRemediate,
    RunCompleted,
    RunFailed,
    PlanAwaitingClarification,
    RatchetTrigger,
    ROUTING,
)
from contracts.version import CONTRACTS_VERSION, __version__

__all__ = [
    "PlanRatified",
    "NodeDispatch",
    "NodeSpawned",
    "NodeObserved",
    "GateEvaluated",
    "NodeRemediate",
    "RunCompleted",
    "RunFailed",
    "PlanAwaitingClarification",
    "RatchetTrigger",
    "ROUTING",
    "CONTRACTS_VERSION",
    "__version__",
]
