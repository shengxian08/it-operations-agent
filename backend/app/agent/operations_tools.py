"""Readonly application tools; the same dispatcher is used by Agent and MCP."""

import asyncio
import copy
import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Literal, Self
from uuid import uuid4

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)

from app.agent.decisions import TaskBudget


@dataclass(frozen=True)
class OperationsScope:
    user_id: str
    access_level: str
    services: frozenset[str]


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class KnowledgeArgs(Arguments):
    query: str = Field(min_length=1, max_length=1000)
    limit: int = Field(default=5, strict=True, ge=1, le=10)


class ServiceArgs(Arguments):
    service: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_.-]+$")


class WindowArgs(ServiceArgs):
    start: AwareDatetime
    end: AwareDatetime
    limit: int = Field(default=50, strict=True, ge=1, le=100)

    @model_validator(mode="after")
    def bounded_window(self) -> Self:
        if not 0 < (self.end - self.start).total_seconds() <= 86400:
            raise ValueError("Time window must be positive and <=24 hours")
        return self


class MetricsArgs(WindowArgs):
    metric: Literal["error_rate", "latency_ms", "request_rate", "availability"]


class TicketArgs(Arguments):
    ticket_number: str = Field(min_length=1, max_length=100)


class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(default_factory=lambda: uuid4().hex)
    tool: str
    status: Literal["ok", "unknown"]
    observed_at: AwareDatetime
    origin: Literal["snapshot", "live", "knowledge", "business"]
    snapshot_id: str | None = None
    knowledge_revision: str | None = None
    resource: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None


SCHEMAS = {
    "search_knowledge": KnowledgeArgs,
    "get_service_health": ServiceArgs,
    "query_error_logs": WindowArgs,
    "query_metrics": MetricsArgs,
    "get_release_changes": WindowArgs,
    "lookup_ticket": TicketArgs,
}


class SnapshotReader:
    """Explicit fixture snapshots. No environment discovery or model-controlled endpoints."""

    def __init__(self, services: Mapping, snapshot_id: str):
        self.services = copy.deepcopy(dict(services))
        self.snapshot_id = snapshot_id

    async def read(self, tool, args):
        record = self.services.get(args["service"])
        if not record:
            raise LookupError("snapshot_not_available")
        observed = datetime.fromisoformat(record["observed_at"].replace("Z", "+00:00"))
        if tool == "get_service_health":
            data = record["health"]
        else:
            field = {
                "query_error_logs": "logs",
                "query_metrics": "metrics",
                "get_release_changes": "changes",
            }[tool]
            items = record.get(field)
            if items is None:
                raise LookupError("snapshot_field_unknown")
            rows = [
                row
                for row in items
                if args["start"]
                <= datetime.fromisoformat(row["at"].replace("Z", "+00:00"))
                <= args["end"]
            ]
            if tool == "query_metrics":
                rows = [row for row in rows if row["metric"] == args["metric"]]
            data = {
                "items": rows[: args["limit"]],
                "truncated": len(rows) > args["limit"],
            }
        return observed, copy.deepcopy(data), "snapshot", self.snapshot_id


class OperationsTools:
    def __init__(
        self,
        *,
        reader=None,
        retriever=None,
        ticket_service=None,
        authorize=None,
        timeout_seconds=15,
        audit_sink=None,
    ):
        self.reader, self.retriever, self.ticket_service = (
            reader,
            retriever,
            ticket_service,
        )
        self.authorize, self.timeout_seconds = authorize, timeout_seconds
        self.audit = []
        self.audit_sink = audit_sink

    def definitions(self, names=None):
        return [
            {
                "name": name,
                "input_schema": schema.model_json_schema(),
                "read_only": True,
            }
            for name, schema in SCHEMAS.items()
            if names is None or name in names
        ]

    async def call(self, name, arguments, scope: OperationsScope, budget: TaskBudget):
        budget.take_tool()
        result = await self._call(name, arguments, scope, budget)
        record = {
            "tool": name,
            "user_id": scope.user_id,
            "status": result.status,
            "error_code": result.error_code,
            "observation_id": result.id,
            "resource": result.resource,
            "arguments_sha256": hashlib.sha256(
                json.dumps(arguments, default=str, sort_keys=True).encode()
            ).hexdigest(),
            "at": datetime.now(timezone.utc).isoformat(),
        }
        self.audit.append(record)
        self.audit[:] = self.audit[-100:]
        if self.audit_sink is not None:
            await self.audit_sink(record)
        return result

    async def _call(self, name, arguments, scope, budget):
        origin = (
            "knowledge"
            if name == "search_knowledge"
            else "business"
            if name == "lookup_ticket"
            else "snapshot"
        )

        def unknown(code):
            return Observation(
                tool=name,
                status="unknown",
                observed_at=datetime.now(timezone.utc),
                origin=origin,
                error_code=code,
            )

        if name not in SCHEMAS:
            return unknown("tool_not_allowed")
        try:
            parsed = SCHEMAS[name].model_validate(arguments)
        except ValidationError:
            return unknown("invalid_arguments")
        args = parsed.model_dump()
        if "service" in args and args["service"] not in scope.services:
            return unknown("forbidden_resource")
        try:
            if self.authorize is not None and not await self.authorize(
                scope, name, args
            ):
                return unknown("authorization_changed")
            async with asyncio.timeout(
                min(self.timeout_seconds, budget.remaining_seconds())
            ):
                if name == "search_knowledge":
                    if self.retriever is None:
                        return unknown("knowledge_unavailable")
                    hits = await self.retriever.retrieve(
                        args["query"],
                        user_access_level=scope.access_level,
                        limit=args["limit"],
                    )
                    data = {
                        "hits": [
                            {"citation": asdict(h.citation), "score": h.score}
                            for h in hits
                        ]
                    }
                    relation_context = getattr(self.retriever, "latest_relations", None)
                    if isinstance(relation_context, dict) and relation_context:
                        data["relation_context"] = relation_context
                    revisions = {h.citation.index_revision for h in hits}
                    result = Observation(
                        tool=name,
                        status="ok",
                        observed_at=datetime.now(timezone.utc),
                        origin="knowledge",
                        knowledge_revision=next(iter(revisions))
                        if len(revisions) == 1
                        else None,
                        data=data,
                    )
                elif name == "lookup_ticket":
                    if self.ticket_service is None:
                        return unknown("ticket_unavailable")
                    ticket = await self.ticket_service.get_ticket_status(
                        scope.user_id, args["ticket_number"]
                    )
                    result = Observation(
                        tool=name,
                        status="ok",
                        observed_at=datetime.now(timezone.utc),
                        origin="business",
                        data=ticket.model_dump(mode="json"),
                    )
                else:
                    if self.reader is None:
                        return unknown("operations_unavailable")
                    observed, data, data_origin, snapshot = await self.reader.read(
                        name, args
                    )
                    result = Observation(
                        tool=name,
                        status="ok",
                        observed_at=observed,
                        origin=data_origin,
                        snapshot_id=snapshot,
                        resource=args["service"],
                        data=data,
                    )
            if self.authorize is not None and not await self.authorize(
                scope, name, args
            ):
                return unknown("authorization_changed")
            if len(result.model_dump_json().encode()) > 24000:
                return unknown("result_too_large")
            return result
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            return unknown("tool_timeout")
        except Exception:
            return unknown("tool_unavailable")
