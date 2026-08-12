from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import AgentRun, Message


@dataclass(frozen=True, slots=True)
class StartedRun:
    message_id: str
    run_id: str


class AgentRunRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def start(
        self,
        *,
        conversation_id: str,
        content: str,
        trace_id: str,
    ) -> StartedRun:
        async with self._session_factory.begin() as session:
            message = Message(
                conversation_id=conversation_id,
                role="user",
                content=content,
                citations=[],
            )
            session.add(message)
            await session.flush()
            run = AgentRun(
                conversation_id=conversation_id,
                message_id=message.id,
                status="running",
                trace_id=trace_id,
                node_history=[],
            )
            session.add(run)
            await session.flush()
            return StartedRun(message_id=message.id, run_id=run.id)

    async def finish(
        self,
        *,
        run_id: str,
        conversation_id: str,
        answer: str,
        citations: list[dict[str, Any]],
        intent: Any,
        final_state: str,
        handoff_reason: Any,
        error_type: Any,
        node_history: list[dict[str, Any]],
        latency_ms: int,
        model_name: str | None,
        input_tokens: int,
        output_tokens: int,
    ) -> str:
        async with self._session_factory.begin() as session:
            run = await session.get(AgentRun, run_id)
            if run is None:
                raise RuntimeError("agent run is missing")
            assistant_message = Message(
                conversation_id=conversation_id,
                role="assistant",
                content=answer,
                citations=citations,
            )
            session.add(assistant_message)
            await session.flush()
            run.result_message_id = assistant_message.id
            run.status = "completed" if final_state != "handoff" else "handoff"
            run.intent = str(intent) if intent is not None else None
            run.final_state = final_state
            run.node_history = node_history
            run.latency_ms = latency_ms
            run.model_name = model_name
            run.input_tokens = input_tokens
            run.output_tokens = output_tokens
            run.handoff_reason = (
                str(handoff_reason) if handoff_reason is not None else None
            )
            run.error_type = str(error_type) if error_type is not None else None
            run.finished_at = datetime.now(UTC)
            return assistant_message.id

    async def handoff(
        self,
        *,
        run_id: str,
        node_history: list[dict[str, Any]],
        latency_ms: int,
        reason: str = "persistence_failed",
    ) -> None:
        async with self._session_factory.begin() as session:
            run = await session.get(AgentRun, run_id)
            if run is None:
                return
            run.status = "handoff"
            run.final_state = "handoff"
            run.handoff_reason = reason
            run.error_type = reason
            run.node_history = node_history
            run.latency_ms = latency_ms
            run.finished_at = datetime.now(UTC)
