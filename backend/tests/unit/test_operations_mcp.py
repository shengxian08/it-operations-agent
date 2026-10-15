import importlib

import pytest

from app.agent.operations_tools import OperationsTools, OperationsScope, SnapshotReader


@pytest.mark.asyncio
async def test_independent_mcp_client_reads_snapshot_with_same_tool_contract():
    try:
        module = importlib.import_module("app.agent.mcp_server")
    except ModuleNotFoundError:
        pytest.fail("The official SDK MCP server and client bridge are missing")
    from mcp import Client

    tools = OperationsTools(
        reader=SnapshotReader(
            {
                "checkout": {
                    "observed_at": "2026-10-01T08:00:00Z",
                    "health": {"reachable": False},
                }
            },
            "synthetic-mcp-fixture",
        )
    )

    async def scope(ctx):
        return OperationsScope("synthetic", "employee", frozenset({"checkout"}))

    server = module.build_operations_mcp(tools, scope)
    async with Client(server) as client:
        listing = await client.list_tools()
        assert {t.name for t in listing.tools} == {
            "search_knowledge",
            "get_service_health",
            "query_error_logs",
            "query_metrics",
            "get_release_changes",
            "lookup_ticket",
        }
        result = await client.call_tool(
            "get_service_health", {"params": {"service": "checkout"}}
        )
        assert not result.is_error
        assert result.structured_content["origin"] == "snapshot"
        assert result.structured_content["data"] == {"reachable": False}
        denied = await client.call_tool(
            "get_service_health", {"params": {"service": "secret"}}
        )
        assert denied.structured_content["error_code"] == "forbidden_resource"


@pytest.mark.asyncio
async def test_workbench_investigator_uses_mcp_bridge_and_parent_counts():
    from app.agent.investigation import Investigator
    from app.llm.providers import MockChatProvider
    from app.agent.decisions import TaskBudget

    tools = OperationsTools(
        reader=SnapshotReader(
            {
                "checkout": {
                    "observed_at": "2026-10-01T08:00:00Z",
                    "health": {"reachable": False},
                }
            },
            "fixture",
        )
    )
    budget = TaskBudget()
    result = await Investigator(MockChatProvider(), tools, transport="mcp").run(
        "调查checkout",
        scope=OperationsScope("u", "employee", frozenset({"checkout"})),
        budget=budget,
    )
    assert result["observations"][0]["origin"] == "snapshot"
    assert budget.tool_calls == 1


@pytest.mark.asyncio
async def test_http_mcp_rejects_missing_session_and_csrf_before_protocol_dispatch():
    from types import SimpleNamespace
    from fastapi import FastAPI, HTTPException
    from httpx import AsyncClient, ASGITransport
    from app.production.identity import Principal
    from app.agent.mcp_server import AuthenticatedMcpApp

    called = []

    async def protocol(scope, receive, send):
        called.append(scope)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    class Identity:
        settings = SimpleNamespace(
            session_cookie_name="session", public_base_url="http://localhost:5173"
        )

        async def principal(self, cookie):
            if cookie != "valid":
                raise HTTPException(401, "identity_required")
            return Principal(
                user_id="u", display_name="User", role="employee", csrf_token="token"
            )

    app = FastAPI()
    app.state.identity_service = Identity()
    app.mount("/agent-tools", AuthenticatedMcpApp(protocol, app))
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://localhost"
    ) as client:
        assert (await client.post("/agent-tools/mcp", json={})).status_code == 401
        client.cookies.set("session", "valid")
        assert (await client.post("/agent-tools/mcp", json={})).status_code == 403
        assert (
            await client.post(
                "/agent-tools/mcp",
                json={},
                headers={"Origin": "http://localhost:5173", "X-CSRF-Token": "token"},
            )
        ).status_code == 200
    assert len(called) == 1
