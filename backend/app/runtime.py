"""Shared connection and provider lifecycle for HTTP and durable workers."""

import asyncio
from dataclasses import dataclass
from typing import Any

from qdrant_client import AsyncQdrantClient
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.agent.graph import GraphDependencies
from app.llm.providers import MockChatProvider, OpenAICompatibleProvider
from app.services.chat import ChatService


@dataclass
class Runtime:
    settings: Any
    engine: Any
    session_factory: Any
    redis: Any
    qdrant: Any
    read_qdrant: Any
    provider: Any
    ticket_service: Any
    knowledge_service: Any


_runtimes: dict[int, Runtime] = {}


async def open_runtime(settings) -> Runtime:
    loop_key = id(asyncio.get_running_loop())
    existing = _runtimes.get(loop_key)
    if existing:
        return existing
    from app.production.business import ProductionTicketService
    from app.production.knowledge import KnowledgeService

    engine = create_async_engine(
        settings.database_url, pool_pre_ping=True, pool_size=10, max_overflow=5
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    query_key = (
        settings.qdrant_api_key
        if settings.runtime_role == "worker"
        else settings.qdrant_read_api_key
    )
    qdrant = AsyncQdrantClient(
        url=str(settings.qdrant_url),
        api_key=query_key.get_secret_value() if query_key else None,
        check_compatibility=False,
        timeout=10,
    )
    reader = AsyncQdrantClient(
        url=str(settings.qdrant_url),
        api_key=settings.qdrant_read_api_key.get_secret_value()
        if settings.qdrant_read_api_key
        else None,
        check_compatibility=False,
        timeout=10,
    )
    provider = (
        MockChatProvider()
        if settings.model_mode == "mock"
        else OpenAICompatibleProvider(settings)
    )
    runtime = Runtime(
        settings,
        engine,
        sessions,
        redis,
        qdrant,
        reader,
        provider,
        ProductionTicketService(sessions, settings),
        KnowledgeService(sessions, qdrant, settings),
    )
    _runtimes[loop_key] = runtime
    return runtime


async def build_chat_service(
    settings, user_access_level="employee", index_revision=None, *, user_id=None
) -> ChatService:
    from app.production.knowledge import build_retriever

    runtime = await open_runtime(settings)
    retriever = await build_retriever(
        runtime.session_factory,
        runtime.read_qdrant,
        settings,
        index_revision=index_revision,
    )
    from app.production.relations import (
        RelationStore,
        RelationRetriever,
        CurrentAccessRetriever,
    )
    from app.agent.investigation import Investigator
    from app.agent.operations_tools import OperationsScope
    from app.agent.decisions import TaskBudget

    store = RelationStore(runtime.session_factory)
    effective_revision = getattr(
        getattr(retriever, "revision", None), "id", index_revision
    )
    if user_id:
        retriever = CurrentAccessRetriever(
            retriever, store, runtime.session_factory, user_id, user_access_level
        )
    if user_id and effective_revision and settings.relation_retrieval_enabled:
        graph_version = await store.active_version(user_id, effective_revision)
        retriever = RelationRetriever(
            retriever, store, user_id, effective_revision, graph_version or ""
        )
    from app.agent.agentic_rag import AgenticRetriever

    async def read_source(citation, level):
        if user_id is None:
            raise ValueError("Trusted identity is required")
        return await store.read_citation(user_id, citation, level)

    agentic = (
        AgenticRetriever(
            retriever,
            runtime.provider,
            source_reader=read_source,
            minimum_score=settings.minimum_evidence_score,
        )
        if settings.agentic_rag_enabled
        else None
    )
    scope = (
        OperationsScope(
            user_id,
            user_access_level,
            frozenset(settings.investigation_allowed_services),
        )
        if user_id
        else None
    )
    tools = (
        await build_operations_tools(settings, scope, retriever=retriever)
        if scope
        else None
    )
    investigator = (
        Investigator(
            runtime.provider,
            tools,
            strategy=settings.investigation_strategy,
            transport="mcp" if settings.mcp_enabled else "direct",
        )
        if tools
        else None
    )

    def budget_factory():
        return TaskBudget(
            max_tool_calls=settings.agent_tool_limit,
            max_model_calls=settings.agent_model_limit,
            max_tokens=settings.agent_token_limit,
            timeout_seconds=settings.run_timeout_seconds,
            output_reserve=settings.model_max_output_tokens,
            max_attempts=settings.model_max_retries + 1,
        )

    return ChatService(
        runtime.session_factory,
        GraphDependencies(
            retriever,
            runtime.provider,
            runtime.ticket_service,
            user_access_level,
            settings.minimum_evidence_score,
            True,
            agentic,
            investigator,
            scope,
            budget_factory,
        ),
        runtime.ticket_service,
    )


async def build_operations_tools(settings, scope, *, retriever=None):
    import json
    from app.agent.operations_tools import OperationsTools, SnapshotReader
    from app.production.ticket_progress import current_role

    runtime = await open_runtime(settings)
    if retriever is None:
        from app.production.knowledge import build_retriever

        retriever = await build_retriever(
            runtime.session_factory, runtime.read_qdrant, settings
        )
        from app.production.relations import CurrentAccessRetriever, RelationStore

        retriever = CurrentAccessRetriever(
            retriever,
            RelationStore(runtime.session_factory),
            runtime.session_factory,
            scope.user_id,
            scope.access_level,
        )
    reader = None
    if settings.operations_snapshot_path is not None:
        if settings.operations_snapshot_path.stat().st_size > 1024 * 1024:
            raise ValueError("Snapshot is too large")
        value = json.loads(
            settings.operations_snapshot_path.read_text(encoding="utf-8")
        )
        reader = SnapshotReader(value["services"], value["snapshot_id"])
    if settings.operations_kubeconfig is not None:
        if reader is not None:
            raise ValueError('Choose snapshot or live Kubernetes source explicitly')
        from app.agent.kubernetes_reader import KubernetesReader
        reader=KubernetesReader(settings.operations_kubeconfig,settings.operations_kube_context or '',
            settings.operations_kube_targets,binary=settings.operations_kubectl_binary)

    async def authorize(current, tool, arguments):
        async with runtime.session_factory() as session:
            role = await current_role(session, current.user_id)
            ranks = {"employee": 0, "support": 1, "admin": 2}
            return (
                ranks[role] >= ranks.get(current.access_level, 100)
                and current.user_id == scope.user_id
            )

    async def audit(record):
        from app.production.business_models import BusinessAudit

        async with runtime.session_factory.begin() as session:
            session.add(
                BusinessAudit(
                    entity_type="agent_tool",
                    entity_id=record["observation_id"],
                    actor_id=scope.user_id,
                    event_type="readonly_observation",
                    details=record,
                )
            )

    return OperationsTools(
        reader=reader,
        retriever=retriever,
        ticket_service=runtime.ticket_service,
        authorize=authorize,
        audit_sink=audit,
    )


async def close_runtime() -> None:
    runtime = _runtimes.pop(id(asyncio.get_running_loop()), None)
    if not runtime:
        return
    closer = getattr(runtime.provider, "close", None)
    if closer:
        await closer()
    await runtime.qdrant.close()
    await runtime.read_qdrant.close()
    await runtime.redis.aclose()
    await runtime.engine.dispose()
