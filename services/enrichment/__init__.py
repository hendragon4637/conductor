"""enrichment-svc — tool catalog enrichment pipeline.

Discovers new tool candidates from external sources (GitHub topics, MCP
registries, package registries), filters by maturity thresholds, deduplicates
against the existing catalog, and inserts qualifying candidates.

Also provides the gap-trigger mechanism that watches failure events for
missing-tool signals and fires targeted enrichment on demand.
"""

from __future__ import annotations
