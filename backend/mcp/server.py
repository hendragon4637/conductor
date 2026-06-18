"""Conductor MCP server — exposed over SSE for remote chat clients.

Usage (start standalone)::

    CONDUCTOR_MCP_TOKEN=my-secret uv run python -m backend.mcp.server

This starts the MCP server on the configured host/port, bound to
the LAN interface so Claude Desktop on the human PC can reach it over
the network.

Security:
    - Token required in ``Authorization: Bearer <token>`` header.
    - Only read + pending-create tools are exposed. No approve/spawn/delete.
    - Proposals are validated by Conductor's existing spec.
"""

from __future__ import annotations

import json
import logging
import os

import uvicorn
from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from backend.mcp import tools

logger = logging.getLogger(__name__)

MCP_HOST = os.environ.get("CONDUCTOR_MCP_HOST", "127.0.0.1")
MCP_PORT = int(os.environ.get("CONDUCTOR_MCP_PORT", "8092"))
MCP_TOKEN = os.environ.get("CONDUCTOR_MCP_TOKEN", "")
MCP_SSE_PATH = os.environ.get("CONDUCTOR_MCP_SSE_PATH", "/sse")
MCP_MESSAGE_PATH = os.environ.get("CONDUCTOR_MCP_MESSAGE_PATH", "/messages/")


def create_mcp_app() -> FastMCP:
    """Build and return a configured FastMCP instance with all tools registered."""
    mcp = FastMCP(
        name="Conductor",
        instructions=(
            "Conductor plan orchestrator. Use these tools to create and inspect "
            "plans for the AI PC automation system. All plan mutations produce "
            "PENDING plans — approval happens in the Conductor UI, not via MCP."
        ),
        host=MCP_HOST,
        port=MCP_PORT,
        sse_path=MCP_SSE_PATH,
        message_path=MCP_MESSAGE_PATH,
        log_level="INFO",
        warn_on_duplicate_tools=True,
    )

    @mcp.tool(name="conductor-create_plan", description="Create a new pending plan from intent, optional spec, optional quality intent, or a pre-decomposed DAG (nodes).")
    async def create_plan(
        intent: str,
        spec: str | None = None,
        quality_intent: str | None = None,
        project: str | None = None,
        nodes: str | None = None,
    ) -> str:
        """Create a pending plan from intent with optional dual input or pre-decomposed DAG.

        Dual-input model (File 04):
        - ``intent`` (required): The primary goal, e.g. "Build a money-transfer API".
        - ``spec`` (optional): Additional structured constraints for the brain prompt.
        - ``quality_intent`` (optional): Free-text quality requirements for check generation.
        - ``nodes`` (optional): JSON string of a pre-decomposed DAG (canonical Node[]).
          When provided, SKIPS the brain, validates the DAG (per-member backend, deps resolve, acyclic),
          still generates checks + plan.success, still requires ratification.

        Args:
            intent: Natural-language description of what you want done.
            spec: Optional structured constraints for the plan brain.
            quality_intent: Optional free-text quality requirements.
            project: Optional project name (defaults to "default").
            nodes: Optional JSON string of pre-decomposed DAG (BYO-DAG path).
        """
        result = await tools.handle_create_plan(
            intent, project or "default",
            spec=spec, quality_intent=quality_intent, nodes=nodes,
        )
        return json.dumps(result, indent=2)

    @mcp.tool(name="conductor-refine_plan", description="Refine an existing pending plan with an instruction and optional new quality intent.")
    async def refine_plan(
        plan_id: str,
        instruction: str,
        quality_intent: str | None = None,
    ) -> str:
        """Re-decompose an existing plan with a refinement instruction.
        The plan stays pending — re-ratification is required in the UI.
        Optionally override the plan's quality intent for check regeneration.

        Args:
            plan_id: The plan ID to refine.
            instruction: Natural-language description of the refinement.
            quality_intent: Optional new quality intent to override the
                plan's existing quality requirements.
        """
        result = await tools.handle_refine_plan(plan_id, instruction, quality_intent=quality_intent)
        return json.dumps(result, indent=2)

    @mcp.tool(name="conductor-get_plan", description="Get full plan details including DAG and per-node checks.")
    async def get_plan(
        plan_id: str,
    ) -> str:
        """Retrieve a plan's complete state: DAG nodes, success criteria, generated checks,
        ratification status.

        Args:
            plan_id: The plan ID to retrieve.
        """
        result = await tools.handle_get_plan(plan_id)
        return json.dumps(result, indent=2)

    @mcp.tool(name="conductor-list_sessions", description="List all running and completed sessions with status.")
    async def list_sessions() -> str:
        """Return the current state of all sessions managed by the watcher.
        Shows session_id, project, status, and current node.
        """
        result = await tools.handle_list_sessions()
        return json.dumps(result, indent=2)

    @mcp.tool(name="conductor-search_memory", description="Search product or meta memory for project conventions and past patterns.")
    async def search_memory(
        query: str,
        scope: str = "product",
    ) -> str:
        """Search Neo4j product memory for project conventions, past error patterns,
        and architecture decisions relevant to the query.

        Args:
            query: The search query describing what you're looking for.
            scope: Memory scope — "product" for project-specific, "meta" for architecture decisions.
        """
        result = await tools.handle_search_memory(query, scope)
        return json.dumps(result, indent=2)

    return mcp


def _token_auth_middleware(app: Starlette) -> Starlette:
    if not MCP_TOKEN:
        logger.warning("CONDUCTOR_MCP_TOKEN is empty — auth DISABLED")
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
    mcp = create_mcp_app()
    sse_app = mcp.sse_app()
    authed_app = _token_auth_middleware(sse_app)
    logger.info(
        "Starting Conductor MCP server on %s:%s (auth: %s)",
        MCP_HOST, MCP_PORT,
        "enabled" if MCP_TOKEN else "DISABLED",
    )
    uvicorn.run(
        authed_app,
        host=MCP_HOST,
        port=MCP_PORT,
        log_level="info",
    )


if __name__ == "__main__":
    main()
