"""Hermes Agent HTTP adapter — self-orchestrating execution backend.

Hermes (Nous Research v0.16.0) is a second execution backend alongside
AionUi. It receives one goal per node from Conductor, self-decomposes
internally, and routes to its own subagents.

Conductor communicates with Hermes via its HTTP API (``/v1/runs``).
"""

from __future__ import annotations

from .client import HermesClient

__all__ = ["HermesClient"]
