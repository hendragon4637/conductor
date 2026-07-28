"""Gap trigger: watch failure_events for tool-related failures and fire targeted enrichment."""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# Error patterns that indicate a missing/unknown tool
GAP_PATTERNS = [
    (r"tool_not_found:\s*(\S+)", "Tool referenced in capability not found in catalog"),
    (r"skill_install_failed:\s*(\S+)", "Skill installation failed — may need catalog entry"),
    (r"mcp_connection_refused:\s*(\S+)", "MCP server connection failed — may need catalog entry"),
    (r"unknown tool[\s:]+(\S+)", "Unknown tool referenced"),
]


def extract_tool_name(error_type: str, error_message: str) -> str | None:
    """Extract tool name from error payload."""
    combined = f"{error_type}: {error_message}"
    for pattern, _ in GAP_PATTERNS:
        m = re.search(pattern, combined, re.IGNORECASE)
        if m:
            return m.group(1)
    return None


def handle_failure_event(db_url: str, event_payload: dict[str, Any]) -> dict[str, Any] | None:
    """Process a failure event and trigger targeted enrichment if applicable.

    Returns None if no gap was detected, or the enrich_candidate result dict.
    """
    error_type = event_payload.get("error_type", "")
    error_message = event_payload.get("error", "") or event_payload.get("message", "") or str(event_payload.get("metadata", {}))

    tool_name = extract_tool_name(error_type, error_message)
    if not tool_name:
        return None

    logger.info("Gap trigger detected: tool=%s from error=%s", tool_name, error_type)

    from services.enrichment.enrich_catalog import enrich_candidate
    return enrich_candidate(db_url, tool_name)
