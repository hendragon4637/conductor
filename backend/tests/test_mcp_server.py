"""Tests for MCP server: tool registration, auth middleware, Obsidian vault."""

from __future__ import annotations

from starlette.testclient import TestClient

from backend.mcp.server import create_mcp_app, _token_auth_middleware
from backend.obsidian_mcp.server import create_obsidian_mcp


def test_mcp_tools_registered():
    mcp = create_mcp_app()
    tools = mcp._tool_manager.list_tools()
    names = [t.name for t in tools]
    assert "conductor-create_plan" in names
    assert "conductor-refine_plan" in names
    assert "conductor-get_plan" in names
    assert "conductor-list_sessions" in names
    assert "conductor-search_memory" in names
    assert len(names) == 5


def test_mcp_no_approve_spawn_tools():
    mcp = create_mcp_app()
    tools = mcp._tool_manager.list_tools()
    names = [t.name for t in tools]
    disallowed = ["approve", "spawn", "delete", "cancel", "reject"]
    for dis in disallowed:
        assert not any(dis in name for name in names), f"Found disallowed tool: {dis}"


def test_mcp_tool_parameters():
    mcp = create_mcp_app()
    tools = mcp._tool_manager.list_tools()
    tool_map = {t.name: t for t in tools}

    create = tool_map["conductor-create_plan"]
    params = create.parameters
    props = params.get("properties", {})
    assert "intent" in props

    search = tool_map["conductor-search_memory"]
    search_params = search.parameters
    search_props = search_params.get("properties", {})
    assert "query" in search_props
    assert "scope" in search_props


def test_auth_middleware_rejects_no_token(monkeypatch):
    monkeypatch.setenv("CONDUCTOR_MCP_TOKEN", "required-token")
    import backend.mcp.server as mcp_srv
    mcp_srv.MCP_TOKEN = "required-token"

    from starlette.applications import Starlette
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route

    test_app = Starlette(routes=[
        Route("/health", lambda r: PlainTextResponse("ok")),
    ])
    wrapped = _token_auth_middleware(test_app)
    client = TestClient(wrapped)
    response = client.get("/health")
    assert response.status_code == 401


def test_auth_middleware_rejects_wrong_token(monkeypatch):
    from starlette.applications import Starlette
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route

    monkeypatch.setenv("CONDUCTOR_MCP_TOKEN", "secret123")
    import backend.mcp.server as mcp_srv
    mcp_srv.MCP_TOKEN = "secret123"

    test_app = Starlette(routes=[
        Route("/health", lambda r: PlainTextResponse("ok")),
    ])
    wrapped = _token_auth_middleware(test_app)
    client = TestClient(wrapped)
    response = client.get("/health", headers={"Authorization": "Bearer wrong-token"})
    assert response.status_code == 401


def test_auth_middleware_passes_with_valid_token(monkeypatch):
    from starlette.applications import Starlette
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route

    monkeypatch.setenv("CONDUCTOR_MCP_TOKEN", "valid-token")
    import backend.mcp.server as mcp_srv
    mcp_srv.MCP_TOKEN = "valid-token"

    test_app = Starlette(routes=[
        Route("/health", lambda r: PlainTextResponse("ok")),
    ])
    wrapped = _token_auth_middleware(test_app)
    client = TestClient(wrapped)
    response = client.get("/health", headers={"Authorization": "Bearer valid-token"})
    assert response.status_code == 200


def test_obsidian_mcp_resources():
    mcp = create_obsidian_mcp()
    resources = mcp._resource_manager.list_resources()
    assert len(resources) >= 1
    uris = [r.uri for r in resources]
    assert all(str(u).startswith("obsidian://") for u in uris)


def test_obsidian_mcp_tool():
    mcp = create_obsidian_mcp()
    tools = mcp._tool_manager.list_tools()
    names = [t.name for t in tools]
    assert "obsidian-read_note" in names
    assert len(names) == 1


def test_obsidian_read_note_tool_parameters():
    mcp = create_obsidian_mcp()
    tools = mcp._tool_manager.list_tools()
    read_tool = [t for t in tools if t.name == "obsidian-read_note"][0]
    params = read_tool.parameters
    assert "uri" in params.get("properties", {})


def test_obsidian_mcp_no_approve_tools():
    mcp = create_obsidian_mcp()
    tools = mcp._tool_manager.list_tools()
    names = [t.name for t in tools]
    disallowed = ["approve", "spawn", "delete", "cancel", "reject", "create_plan"]
    for dis in disallowed:
        assert not any(dis in name for name in names), f"Found disallowed tool: {dis}"
