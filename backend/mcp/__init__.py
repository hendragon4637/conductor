"""Conductor MCP server — chat-driven plan creation from remote clients.

Exposes a safe subset of Conductor operations as MCP tools over SSE
transport, intended for remote chat clients (Claude Desktop on human PC)
to propose plans and read state without bypassing human approval gates.
"""

from __future__ import annotations

from backend.mcp.server import create_mcp_app

__all__ = ["create_mcp_app"]
