from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from qdrant_client import AsyncQdrantClient
from redis.asyncio import Redis
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.agent.graph import GraphDependencies
from app.api.routes import chat, tickets
from app.api.routes.tickets import ApiProblem
from app.core.config import get_settings
from app.db.session import async_session_factory
from app.llm.providers import MockChatProvider, OpenAICompatibleProvider
from app.rag.ingest import DeterministicEmbedder
from app.rag.retriever import HybridRetriever, LexicalReranker
from app.repositories.tickets import TicketRepository
from app.services.chat import ChatService
from app.tickets.service import TicketService

app = FastAPI(title="IT Operations Agent API", version="0.1.0")


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


app.state.chat_service = _build_chat_service()


@app.middleware("http")
async def attach_trace_id(request: Request, call_next):  # type: ignore[no-untyped-def]
    received_trace_id = request.headers.get("X-Trace-Id", "").strip()
    request.state.trace_id = received_trace_id or str(uuid4())
    response = await call_next(request)
    response.headers["X-Trace-Id"] = request.state.trace_id
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
    return _error_response(
        request,
        status_code=error.status_code,
        code="not_found" if error.status_code == 404 else "http_error",
        message="Resource was not found."
        if error.status_code == 404
        else "Request could not be completed.",
    )


@app.exception_handler(Exception)
async def handle_unexpected_error(request: Request, error: Exception) -> JSONResponse:
    del error
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
) -> JSONResponse:
    trace_id = getattr(request.state, "trace_id", str(uuid4()))
    return JSONResponse(
        status_code=status_code,
        content={"code": code, "message": message, "trace_id": trace_id},
        headers={"X-Trace-Id": trace_id},
    )


app.include_router(chat.router)
app.include_router(tickets.router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {
        "service": "it-operations-agent-api",
        "status": "ok",
    }
