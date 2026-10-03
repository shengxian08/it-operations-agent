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
    engine = create_async_engine(settings.database_url, pool_pre_ping=True, pool_size=10, max_overflow=5)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    query_key = settings.qdrant_api_key if settings.runtime_role == "worker" else settings.qdrant_read_api_key
    qdrant = AsyncQdrantClient(url=str(settings.qdrant_url),api_key=query_key.get_secret_value() if query_key else None,check_compatibility=False,timeout=10)
    reader = AsyncQdrantClient(url=str(settings.qdrant_url),api_key=settings.qdrant_read_api_key.get_secret_value() if settings.qdrant_read_api_key else None,check_compatibility=False,timeout=10)
    provider = MockChatProvider() if settings.model_mode == "mock" else OpenAICompatibleProvider(settings)
    runtime = Runtime(settings,engine,sessions,redis,qdrant,reader,provider,ProductionTicketService(sessions,settings),KnowledgeService(sessions,qdrant,settings))
    _runtimes[loop_key] = runtime
    return runtime


async def build_chat_service(settings, user_access_level="employee", index_revision=None) -> ChatService:
    from app.production.knowledge import build_retriever
    runtime = await open_runtime(settings)
    retriever = await build_retriever(runtime.session_factory,runtime.read_qdrant,settings,index_revision=index_revision)
    return ChatService(runtime.session_factory,GraphDependencies(retriever,runtime.provider,runtime.ticket_service,user_access_level,settings.minimum_evidence_score,True),runtime.ticket_service)


async def close_runtime() -> None:
    runtime = _runtimes.pop(id(asyncio.get_running_loop()),None)
    if not runtime:
        return
    closer = getattr(runtime.provider,"close",None)
    if closer:
        await closer()
    await runtime.qdrant.close()
    await runtime.read_qdrant.close()
    await runtime.redis.aclose()
    await runtime.engine.dispose()
