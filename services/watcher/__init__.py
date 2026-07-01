"""watcher-svc — standalone polling watcher for node execution progress.

Decoupled from the monolithic backend.watcher loop.  Queries
``node_sessions`` with ``verdict=NULL``, derives verdicts via
``backend.watcher.signals`` signal sources, and emits ``NodeObserved``
through the transactional outbox when a terminal verdict is reached.
"""

from __future__ import annotations
