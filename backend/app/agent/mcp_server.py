"""Official MCP SDK interface. Authorization is obtained per call from the host."""

import asyncio
import json
from pathlib import Path

from mcp import Client
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp_types import ToolAnnotations

from app.agent.decisions import TaskBudget
from app.agent.operations_tools import (
    KnowledgeArgs,
    ServiceArgs,
    WindowArgs,
    MetricsArgs,
    TicketArgs,
    Observation,
    OperationsScope,
    OperationsTools,
    SnapshotReader,
)


def build_operations_mcp(tools, scope_provider):
    server = MCPServer(
        "it_operations_mcp",
        instructions="Authorized readonly observations. Snapshot timestamps are historical. Unknown is not healthy or absent.",
    )
    annotations = ToolAnnotations(
        read_only_hint=True,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=False,
    )

    async def call(name, params, ctx):
        scope = await scope_provider(ctx)
        dispatcher = await tools(scope) if callable(tools) else tools
        return await dispatcher.call(
            name,
            params.model_dump(),
            scope,
            TaskBudget(max_tool_calls=1, max_model_calls=1),
        )

    @server.tool(annotations=annotations)
    async def search_knowledge(params: KnowledgeArgs, ctx: Context) -> Observation:
        """Search authorized immutable knowledge; returns original citations/revision. Empty hits are not a model answer."""
        return await call("search_knowledge", params, ctx)

    @server.tool(annotations=annotations)
    async def get_service_health(params: ServiceArgs, ctx: Context) -> Observation:
        """Read health for an authorized service. Inspect origin and observed_at before treating it as current."""
        return await call("get_service_health", params, ctx)

    @server.tool(annotations=annotations)
    async def query_error_logs(params: WindowArgs, ctx: Context) -> Observation:
        """Read at most 100 errors in an aware window of at most 24h. Timeout returns unknown."""
        return await call("query_error_logs", params, ctx)

    @server.tool(annotations=annotations)
    async def query_metrics(params: MetricsArgs, ctx: Context) -> Observation:
        """Read a supported metric for a scoped service and <=24h time window; units remain source-owned."""
        return await call("query_metrics", params, ctx)

    @server.tool(annotations=annotations)
    async def get_release_changes(params: WindowArgs, ctx: Context) -> Observation:
        """Read scoped version/release observations in a bounded time window. Does not deploy or roll back."""
        return await call("get_release_changes", params, ctx)

    @server.tool(annotations=annotations)
    async def lookup_ticket(params: TicketArgs, ctx: Context) -> Observation:
        """Reauthorize and read a specific ticket. Cannot create, confirm or update tickets."""
        return await call("lookup_ticket", params, ctx)

    return server


class McpOperationsTools:
    """Application-side client adapter; scope is enforced by the server's trusted host."""

    def __init__(self, client, definitions):
        self.client = client
        self._definitions = definitions

    def definitions(self, names=None):
        return [d for d in self._definitions if names is None or d["name"] in names]

    async def call(self, name, arguments, scope, budget):
        budget.take_tool()
        async with asyncio.timeout(min(15, budget.remaining_seconds())):
            result = await self.client.call_tool(name, {"params": arguments})
        if result.is_error or result.structured_content is None:
            raise ValueError("MCP returned an invalid tool observation")
        return Observation.model_validate(result.structured_content)


class AuthenticatedMcpApp:
    """Same session/CSRF checks for discovery and every HTTP protocol message."""

    def __init__(self, protocol, application):
        self.protocol, self.application = protocol, application

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            from fastapi import HTTPException
            from starlette.requests import Request
            from starlette.responses import JSONResponse
            from app.production.identity import require_principal

            trusted_scope = {**scope, "app": self.application}
            try:
                await require_principal(Request(trusted_scope, receive))
            except HTTPException as error:
                await JSONResponse(
                    {"code": error.detail},
                    status_code=error.status_code,
                    headers={"Cache-Control": "no-store"},
                )(scope, receive, send)
                return
        await self.protocol(scope, receive, send)


def build_authenticated_http_mcp(application, settings):
    from starlette.requests import Request
    from urllib.parse import urlsplit
    from app.production.identity import require_principal
    from app.runtime import build_operations_tools

    async def trusted_scope(ctx):
        # Headers carry a session credential; they never supply user/role/resource scope.
        headers = [
            (str(k).lower().encode(), str(v).encode())
            for k, v in (ctx.headers or {}).items()
        ]
        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/agent-tools/mcp",
                "headers": headers,
                "app": application,
            }
        )
        principal = await require_principal(request)
        return OperationsScope(
            principal.user_id,
            principal.role,
            frozenset(settings.investigation_allowed_services),
        )

    async def dispatch(scope):
        return await build_operations_tools(settings, scope)

    server = build_operations_mcp(dispatch, trusted_scope)
    protocol = server.streamable_http_app(
        json_response=True,
        stateless_http=True,
        max_request_body_size=65536,
        host=urlsplit(str(settings.public_base_url)).hostname or "127.0.0.1",
    )
    return protocol, AuthenticatedMcpApp(protocol, application)


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Readonly PUBLIC SYNTHETIC snapshot MCP demo; no enterprise identity."
    )
    parser.add_argument("--snapshot", type=Path, required=True)
    args = parser.parse_args()
    data = json.loads(args.snapshot.read_text(encoding="utf-8"))
    tools = OperationsTools(
        reader=SnapshotReader(data["services"], data["snapshot_id"])
    )

    async def scope(ctx):
        return OperationsScope(
            "synthetic-mcp-demo", "employee", frozenset(data["services"])
        )

    build_operations_mcp(tools, scope).run()


if __name__ == "__main__":
    main()
