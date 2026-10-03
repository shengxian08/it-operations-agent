from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from qdrant_client import AsyncQdrantClient
from redis.asyncio import Redis
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.agent.graph import GraphDependencies
from app.api.routes import catalog, chat, tickets
from app.api.routes.tickets import ApiProblem
from app.core.config import get_settings
from app.core.telemetry import get_or_create_trace_id, log_json
from app.db.session import async_session_factory
from app.llm.providers import MockChatProvider, OpenAICompatibleProvider
from app.rag.ingest import DeterministicEmbedder
from app.rag.retriever import HybridRetriever, LexicalReranker
from app.repositories.tickets import TicketRepository
from app.services.chat import ChatService
from app.services.catalog import CatalogService
from app.tickets.service import TicketService
from pathlib import Path
import asyncio
import time
import re
from contextlib import asynccontextmanager
from sqlalchemy import select, text
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from fastapi.responses import Response
from app.core.telemetry import configure_telemetry, log_exception
from app.core.metrics import HTTP_REQUESTS, HTTP_DURATION, DEPENDENCY_ERRORS


@asynccontextmanager
async def lifespan(application):
    settings = get_settings()
    configure_telemetry(settings)
    application.state.settings = settings
    if not settings.demo_enabled:
        from app.runtime import open_runtime, close_runtime
        from app.production.identity import IdentityService
        from app.production.runs import RunService
        runtime = await open_runtime(settings)
        for name in ("session_factory","redis","qdrant","ticket_service","knowledge_service"):
            setattr(application.state,name,getattr(runtime,name))
        application.state.identity_service = IdentityService(settings,runtime.redis,runtime.session_factory)
        application.state.run_service = RunService(settings,runtime.session_factory,runtime.redis)
        try:
            yield
        finally:
            closer = getattr(application.state.identity_service,"close",None)
            if closer:
                await closer()
            await close_runtime()
    else:
        yield

app = FastAPI(title="IT Operations Agent API", version="1.0.0",lifespan=lifespan,
              docs_url="/docs" if get_settings().demo_enabled else None,
              redoc_url=None)


def _build_chat_service() -> ChatService:
    settings = get_settings()
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    ticket_service = TicketService(TicketRepository(async_session_factory), redis)
    chat_provider = (
        MockChatProvider()
        if settings.model_mode == "mock"
        else OpenAICompatibleProvider(settings)
    )
    retriever = HybridRetriever(
        async_session_factory,
        AsyncQdrantClient(
            url=str(settings.qdrant_url),
            check_compatibility=False,
        ),
        DeterministicEmbedder(),
        LexicalReranker(),
    )
    return ChatService(
        async_session_factory,
        GraphDependencies(
            retriever=retriever,
            chat_provider=chat_provider,
            ticket_service=ticket_service,
        ),
        ticket_service,
    )


if get_settings().demo_enabled:
    app.state.chat_service = _build_chat_service()
    app.state.catalog_service = CatalogService(
        async_session_factory,
        Path(__file__).resolve().parents[2] / "data" / "knowledge",
    )


@app.middleware("http")
async def attach_trace_id(request: Request, call_next):  # type: ignore[no-untyped-def]
    started = time.perf_counter()
    request.state.trace_id = get_or_create_trace_id(request)
    response = await call_next(request)
    response.headers["X-Trace-Id"] = request.state.trace_id
    if request.url.path.startswith("/api/v1/"):
        response.headers["Cache-Control"] = "no-store"
    route = getattr(request.scope.get("route"),"path","unmatched")
    HTTP_REQUESTS.labels(request.method,route,str(response.status_code)).inc()
    HTTP_DURATION.labels(route).observe(time.perf_counter()-started)
    log_json(
        "http_request_completed",
        trace_id=request.state.trace_id,
        method=request.method,
        path=route,
        status_code=response.status_code,
    )
    return response


@app.exception_handler(ApiProblem)
async def handle_api_problem(request: Request, error: ApiProblem) -> JSONResponse:
    return _error_response(
        request,
        status_code=error.status_code,
        code=error.code,
        message=error.message,
    )


@app.exception_handler(RequestValidationError)
async def handle_validation_error(
    request: Request,
    error: RequestValidationError,
) -> JSONResponse:
    del error
    return _error_response(
        request,
        status_code=422,
        code="validation_error",
        message="Request validation failed.",
    )


@app.exception_handler(StarletteHTTPException)
async def handle_http_error(
    request: Request,
    error: StarletteHTTPException,
) -> JSONResponse:
    detail = error.detail if isinstance(error.detail, str) else ""
    code = detail if re.fullmatch(r"[a-z][a-z0-9_]{1,80}", detail) else (
        "not_found" if error.status_code == 404 else "http_error"
    )
    return _error_response(
        request,
        status_code=error.status_code,
        code=code,
        message=detail if code == detail else "Resource was not found."
        if error.status_code == 404
        else "Request could not be completed.",
        headers=error.headers,
    )


@app.exception_handler(Exception)
async def handle_unexpected_error(request: Request, error: Exception) -> JSONResponse:
    log_exception("unhandled_error",error,trace_id=get_or_create_trace_id(request))
    return _error_response(
        request,
        status_code=500,
        code="internal_error",
        message="An internal error occurred.",
    )


def _error_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    headers: dict[str,str] | None = None,
) -> JSONResponse:
    trace_id = get_or_create_trace_id(request)
    return JSONResponse(
        status_code=status_code,
        content={"code": code, "message": message, "trace_id": trace_id},
        headers={**(headers or {}), "X-Trace-Id": trace_id, "Cache-Control": "no-store"},
    )


if get_settings().demo_enabled:
    app.include_router(chat.router)
    app.include_router(tickets.router)
    app.include_router(catalog.router)
else:
    from app.production import auth, runs, business, knowledge
    app.include_router(auth.router)
    app.include_router(runs.router)
    app.include_router(business.router)
    app.include_router(knowledge.router)
    app.include_router(knowledge.admin_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {
        "service": "it-operations-agent-api",
        "status": "ok",
    }


@app.get("/health/live")
async def live() -> dict[str,str]:
    return {"service":"it-operations-agent-api","status":"ok"}


@app.get("/health/ready")
async def ready(request: Request):
    settings = get_settings()
    checks = {}
    async def check(name, coroutine):
        try:
            await asyncio.wait_for(coroutine,timeout=2)
            checks[name] = "ok"
        except Exception:
            checks[name] = "unavailable"
            DEPENDENCY_ERRORS.labels(name).inc()
    if settings.demo_enabled:
        from app.runtime import open_runtime
        runtime = await open_runtime(settings)
        sessions,redis,qdrant = runtime.session_factory,runtime.redis,runtime.qdrant
    else:
        sessions,redis,qdrant = request.app.state.session_factory,request.app.state.redis,request.app.state.qdrant
    async def db_check():
        async with sessions() as session:
            await session.execute(text("SELECT 1"))
    async def index_check():
        if settings.demo_enabled:
            await qdrant.get_collection(settings.qdrant_collection)
            return
        result = await request.app.state.knowledge_service.readiness()
        if not result["ready"]:
            raise ValueError("index configuration mismatch")
        if not result.get("revision_id"):
            await qdrant.get_collections()
    await asyncio.gather(check("database",db_check()),check("redis",redis.ping()),check("knowledge_index",index_check()))
    return JSONResponse({"status":"ready" if all(v=="ok" for v in checks.values()) else "unavailable","checks":checks},status_code=200 if all(v=="ok" for v in checks.values()) else 503)


@app.get("/metrics",include_in_schema=False)
async def metrics(request: Request):
    if not get_settings().demo_enabled:
        from app.core.metrics import refresh_durable_metrics
        try:
            await asyncio.wait_for(refresh_durable_metrics(request.app.state.session_factory,get_settings()),timeout=3)
        except Exception as error:
            log_exception("metrics_refresh_failed", error, trace_id=request.state.trace_id)
            return Response("metrics state unavailable\n",status_code=503,media_type="text/plain")
    return Response(generate_latest(),media_type=CONTENT_TYPE_LATEST)
