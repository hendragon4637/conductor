"""Obsidian vault MCP server — serves markdown notes over SSE for remote clients.

The vault at ``/home/aipc/conductor-notes/`` is exposed as MCP resources so
Claude Desktop (on the Windows human PC) can read notes for plan grounding.

Usage::

    OBSIDIAN_VAULT=/home/aipc/conductor-notes uv run python -m backend.obsidian_mcp.server

This starts an MCP server with SSE transport on the configured host/port,
bound to the LAN interface so chat clients can reach it over the network.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.types import Resource as McpResource, TextResourceContents
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)

MCP_HOST = os.environ.get("OBSIDIAN_MCP_HOST", "127.0.0.1")
MCP_PORT = int(os.environ.get("OBSIDIAN_MCP_PORT", "8093"))
MCP_TOKEN = os.environ.get("OBSIDIAN_MCP_TOKEN", "")
VAULT_PATH = Path(os.environ.get("OBSIDIAN_VAULT", "/home/aipc/conductor-notes"))

_notes_cache: dict[str, tuple[str, str]] = {}


def _walk_markdown(vault: Path) -> None:
    if not vault.is_dir():
        logger.warning("Obsidian vault not found at %s", vault)
        return
    for fp in sorted(vault.rglob("*.md")):
        rel = str(fp.relative_to(vault))
        uri = f"obsidian://{rel}"
        _notes_cache[uri] = (fp.stem, str(fp.resolve()))
    logger.info("Serving %d markdown files from %s", len(_notes_cache), vault)


async def _read_resource(uri: str) -> str | None:
    entry = _notes_cache.get(uri)
    if not entry:
        return None
    try:
        return Path(entry[1]).read_text(encoding="utf-8")
    except Exception as exc:
        logger.error("Failed to read %s: %s", entry[1], exc)
        return None


def create_obsidian_mcp() -> FastMCP:
    mcp = FastMCP(
        name="Obsidian Vault",
        instructions=(
            "Read-only access to the Conductor notes Obsidian vault. "
            "Use ``list_resources`` to see available notes, "
            "``read_resource`` to read a note's content."
        ),
        host=MCP_HOST,
        port=MCP_PORT,
        log_level="INFO",
    )

    _walk_markdown(VAULT_PATH)

    for uri, (name, _) in _notes_cache.items():
        mcp.add_resource(
            McpResource(
                uri=uri,
                name=name,
                description=f"Obsidian note: {uri.removeprefix('obsidian://')}",
                mimeType="text/markdown",
            ),
        )

    @mcp.tool(
        name="obsidian-read_note",
        description="Read an Obsidian note by its URI (e.g. obsidian://architecture/notes.md). "
                    "Use list_resources to discover available URIs.",
    )
    async def read_note(uri: str) -> str:
        content = await _read_resource(uri)
        if content is None:
            available = list(_notes_cache.keys())
            return f"Note not found. Available URIs:\n" + "\n".join(available)
        return content

    return mcp


def _token_auth_middleware(app: Starlette) -> Starlette:
    if not MCP_TOKEN:
        logger.warning("OBSIDIAN_MCP_TOKEN is empty — auth DISABLED")
        return app

    class TokenCheckMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            auth_header = request.headers.get("Authorization", "")
            scheme, _, token = auth_header.partition(" ")
            if scheme.lower() != "bearer" or token != MCP_TOKEN:
                return JSONResponse(
                    {"error": "Unauthorized"},
                    status_code=401,
                )
            return await call_next(request)

    app.add_middleware(TokenCheckMiddleware)
    return app


def main() -> None:
    mcp = create_obsidian_mcp()
    sse_app = mcp.sse_app()
    authed_app = _token_auth_middleware(sse_app)
    logger.info(
        "Starting Obsidian MCP on %s:%s (auth: %s, vault: %s)",
        MCP_HOST, MCP_PORT,
        "enabled" if MCP_TOKEN else "DISABLED",
        VAULT_PATH,
    )
    uvicorn.run(
        authed_app,
        host=MCP_HOST,
        port=MCP_PORT,
        log_level="info",
    )


if __name__ == "__main__":
    main()
