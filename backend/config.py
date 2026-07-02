"""Backend configuration — environment-based settings for the Conductor backend.

Feature flags and tunables are read from environment variables at import
time.  Prefer module-level constants over inline ``os.environ.get()`` calls
to keep configuration visible in a single place.
"""

from __future__ import annotations

import os

# ── L4 Gate Evaluation ─────────────────────────────────────────────────
# Controls whether L4 persona/usage-simulation evaluation gates run as
# part of the pipeline.  When disabled (the default), L4 gates are skipped
# with an info log and the run is marked ``l4_status='skipped'``.
#
# Set to ``"true"``, ``"1"``, or ``"yes"`` to enable.
L4_GATES: bool = os.environ.get("L4_GATES", "false").lower() in ("true", "1", "yes")
