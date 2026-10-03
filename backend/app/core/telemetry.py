import json
import logging
import re
import time
import traceback
from logging.handlers import TimedRotatingFileHandler
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from opentelemetry import trace


logger = logging.getLogger(__name__)
tracer = trace.get_tracer("app.agent")

_TRACE_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_PHONE_PATTERN = re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)")
_EMAIL_PATTERN = re.compile(
    r"(?<![\w.+-])[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+(?![\w.-])"
)
_CARD_PATTERN = re.compile(r"(?<!\d)\d{16}(?!\d)")
_SAFE_LOG_FIELDS = {
    "trace_id",
    "run_id",
    "node",
    "latency_ms",
    "result_category",
    "document_ids",
    "ticket_number",
    "model",
    "input_tokens",
    "output_tokens",
    "method",
    "path",
    "status_code",
    "error_type",
    "stack",
}


@dataclass(frozen=True, slots=True)
class ModelUsage:
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    usage_uncertain: bool = False


@dataclass(slots=True)
class _ModelUsageCollector:
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    usage_uncertain: bool = False


_trace_id: ContextVar[str | None] = ContextVar("agent_trace_id", default=None)
_run_id: ContextVar[str | None] = ContextVar("agent_run_id", default=None)
_model_usage: ContextVar[_ModelUsageCollector | None] = ContextVar(
    "agent_model_usage", default=None
)


def get_or_create_trace_id(request: Any) -> str:
    state = getattr(request, "state", None)
    existing = getattr(state, "trace_id", None)
    if isinstance(existing, str) and _TRACE_ID_PATTERN.fullmatch(existing.strip()):
        return existing.strip()

    headers = getattr(request, "headers", {})
    received = str(headers.get("X-Trace-Id", "")).strip()
    return received if _TRACE_ID_PATTERN.fullmatch(received) else str(uuid4())


def redact_sensitive(value: str) -> str:
    redacted = _PHONE_PATTERN.sub("[REDACTED]", value)
    redacted = _EMAIL_PATTERN.sub("[REDACTED]", redacted)
    return _CARD_PATTERN.sub("[REDACTED]", redacted)


def log_json(event: str, **fields: Any) -> None:
    payload: dict[str, Any] = {"event": event}
    for key, value in fields.items():
        if key not in _SAFE_LOG_FIELDS or value is None:
            continue
        payload[key] = _json_safe(value)
    logger.info(json.dumps(payload, ensure_ascii=True, separators=(",", ":")))


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        try:
            payload = json.loads(record.getMessage())
            if not isinstance(payload, dict):
                payload = {"event":"application_log"}
        except (ValueError, TypeError):
            # Third party log messages can contain SQL params/prompts: never forward raw text.
            payload = {"event":"application_log", "logger":record.name}
        payload["level"] = record.levelname
        payload["timestamp"] = record.created
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_telemetry(settings: Any) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    application_logger = logging.getLogger("app")
    application_logger.handlers[:] = [handler]
    if settings.log_directory:
        settings.log_directory.mkdir(parents=True,exist_ok=True)
        file_handler = TimedRotatingFileHandler(settings.log_directory / f"{settings.runtime_role}.log",
            when="midnight",backupCount=30,encoding="utf-8",utc=True)
        file_handler.setFormatter(JsonFormatter())
        application_logger.addHandler(file_handler)
    application_logger.setLevel(settings.log_level)
    application_logger.propagate = False
    if settings.otel_exporter_otlp_endpoint:
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        provider = TracerProvider()
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint.rstrip("/") + "/v1/traces")))
        trace.set_tracer_provider(provider)


def log_exception(event: str, error: Exception, *, trace_id: str) -> None:
    # Keep stack locations, omit exception text/SQL parameters and local variables.
    stack = [{"file":frame.filename.rsplit("/",1)[-1].rsplit("\\",1)[-1], "line":frame.lineno, "function":frame.name}
             for frame in traceback.extract_tb(error.__traceback__)]
    log_json(event, trace_id=trace_id, error_type=type(error).__name__, stack=json.dumps(stack))


@contextmanager
def bind_run(trace_id: str, run_id: str) -> Iterator[None]:
    _trace_id.set(trace_id)
    _run_id.set(run_id)
    _model_usage.set(_ModelUsageCollector())
    try:
        yield
    finally:
        # Streaming generators may resume in another task context after a yield.
        _model_usage.set(None)
        _run_id.set(None)
        _trace_id.set(None)


def current_run_context() -> tuple[str | None, str | None]:
    return _trace_id.get(), _run_id.get()


def add_model_usage(*, model: str, input_tokens: int, output_tokens: int, usage_uncertain: bool = False) -> None:
    collector = _model_usage.get()
    if collector is None:
        return
    collector.model = model
    collector.input_tokens += max(0, input_tokens)
    collector.output_tokens += max(0, output_tokens)
    collector.usage_uncertain = collector.usage_uncertain or usage_uncertain


def consume_model_usage() -> ModelUsage:
    collector = _model_usage.get()
    if collector is None:
        return ModelUsage()
    usage = ModelUsage(
        model=collector.model,
        input_tokens=collector.input_tokens,
        output_tokens=collector.output_tokens,
        usage_uncertain=collector.usage_uncertain,
    )
    collector.model = None
    collector.input_tokens = 0
    collector.output_tokens = 0
    collector.usage_uncertain = False
    return usage


def record_node_span(
    *,
    node: str,
    started_ns: int,
    latency_ms: int,
    result_category: str,
    document_ids: list[str] | None = None,
    ticket_number: str | None = None,
    usage: ModelUsage | None = None,
) -> None:
    usage = usage or ModelUsage()
    trace_id, run_id = current_run_context()
    attributes: dict[str, Any] = {
        "agent.node": node,
        "agent.latency_ms": latency_ms,
        "agent.result_category": result_category,
        "agent.input_tokens": usage.input_tokens,
        "agent.output_tokens": usage.output_tokens,
    }
    if trace_id:
        attributes["agent.trace_id"] = trace_id
    if run_id:
        attributes["agent.run_id"] = run_id
    if document_ids:
        attributes["agent.document_ids"] = document_ids
    if ticket_number:
        attributes["agent.ticket_number"] = ticket_number
    if usage.model:
        attributes["gen_ai.request.model"] = usage.model

    span = tracer.start_span(f"agent.node.{node}", start_time=started_ns)
    for key, value in attributes.items():
        span.set_attribute(key, value)
    span.end(end_time=time.time_ns())
    log_json(
        "agent_node_completed",
        trace_id=trace_id,
        run_id=run_id,
        node=node,
        latency_ms=latency_ms,
        result_category=result_category,
        document_ids=document_ids,
        ticket_number=ticket_number,
        model=usage.model,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
    )


def _json_safe(value: Any) -> Any:
    if isinstance(value, str):
        return redact_sensitive(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return redact_sensitive(str(value))
