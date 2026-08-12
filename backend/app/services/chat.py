import time
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.graph import GraphDependencies, build_graph
from app.db.models import AgentRun, Conversation, Message
from app.schemas import TicketDraft
from app.tickets.service import TicketService


class ConversationNotFoundError(Exception):
    """Raised when a conversation is outside the simulated user's scope."""


class MessageNotFoundError(Exception):
    """Raised when a message cannot receive feedback."""


@dataclass(frozen=True, slots=True)
class ChatEvent:
    name: str
    data: dict[str, Any]


class ChatService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        graph_dependencies: GraphDependencies,
        ticket_service: TicketService,
    ) -> None:
        self._session_factory = session_factory
        self._graph_dependencies = graph_dependencies
        self._ticket_service = ticket_service

    async def stream_message(
        self,
        *,
        user_id: str,
        conversation_id: str,
        content: str,
        trace_id: str,
    ) -> AsyncIterator[ChatEvent]:
        await self._require_conversation_owner(user_id, conversation_id)
        message_id, run_id = await self._start_run(
            conversation_id=conversation_id,
            content=content,
            trace_id=trace_id,
        )
        started_at = time.perf_counter()
        node_history: list[dict[str, Any]] = []
        state: dict[str, Any] = {
            "user_id": user_id,
            "conversation_id": conversation_id,
            "message": content,
            "step_count": 0,
            "trace_id": trace_id,
        }
        yield ChatEvent("run_started", {"run_id": run_id, "message_id": message_id})

        try:
            graph = build_graph(self._graph_dependencies)
            async for updates in graph.astream(state, stream_mode="updates"):
                for node_name, update in updates.items():
                    state.update(update)
                    node_history.append({"node": node_name})
                    yield ChatEvent(
                        "node_completed",
                        {"node": node_name, "step_count": state.get("step_count", 0)},
                    )
        except Exception:
            state.update(
                answer="The request could not be completed. Please contact IT support.",
                citations=[],
                final_state="handoff",
                handoff_reason="processing_failed",
                error="processing_failed",
            )

        citations = [asdict(citation) for citation in state.get("citations", [])]
        yield ChatEvent("citations", {"citations": citations})

        draft = state.get("ticket_draft")
        confirmation_token = state.get("confirmation_token")
        if isinstance(draft, TicketDraft) and isinstance(confirmation_token, str):
            yield ChatEvent(
                "ticket_draft",
                {
                    "draft": draft.model_dump(mode="json"),
                    "confirmation_token": confirmation_token,
                },
            )

        handoff_reason = state.get("handoff_reason")
        if isinstance(handoff_reason, str) and handoff_reason:
            yield ChatEvent("handoff", {"reason": handoff_reason})

        final_state = str(state.get("final_state", "handoff"))
        answer = str(
            state.get(
                "answer",
                "The request could not be completed. Please contact IT support.",
            )
        )
        assistant_message_id = await self._finish_run(
            run_id=run_id,
            conversation_id=conversation_id,
            answer=answer,
            citations=citations,
            intent=state.get("intent"),
            final_state=final_state,
            handoff_reason=handoff_reason,
            error_type=state.get("error"),
            node_history=node_history,
            latency_ms=int((time.perf_counter() - started_at) * 1000),
        )
        yield ChatEvent(
            "final",
            {
                "run_id": run_id,
                "message_id": assistant_message_id,
                "answer": answer,
                "final_state": final_state,
            },
        )

    async def confirm_ticket(
        self,
        *,
        user_id: str,
        conversation_id: str,
        draft: TicketDraft,
        confirmation_token: str,
        idempotency_key: str,
    ) -> dict[str, str]:
        await self._require_conversation_owner(user_id, conversation_id)
        result = await self._ticket_service.create_confirmed(
            user_id=user_id,
            draft=draft,
            confirmation_token=confirmation_token,
            idempotency_key=idempotency_key,
        )
        return result.model_dump()

    async def get_ticket_status(
        self,
        *,
        user_id: str,
        ticket_number: str,
    ) -> dict[str, Any]:
        return (
            await self._ticket_service.get_ticket_status(user_id, ticket_number)
        ).model_dump()

    async def record_feedback(
        self,
        *,
        message_id: str,
        feedback: str,
    ) -> None:
        async with self._session_factory.begin() as session:
            message = await session.get(Message, message_id)
            if message is None:
                raise MessageNotFoundError
            message.user_feedback = {"feedback": feedback}

    async def _require_conversation_owner(
        self,
        user_id: str,
        conversation_id: str,
    ) -> None:
        async with self._session_factory() as session:
            owner_id = await session.scalar(
                select(Conversation.user_id).where(Conversation.id == conversation_id)
            )
        if owner_id != user_id:
            raise ConversationNotFoundError

    async def _start_run(
        self,
        *,
        conversation_id: str,
        content: str,
        trace_id: str,
    ) -> tuple[str, str]:
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
            return message.id, run.id

    async def _finish_run(
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
            run.status = "completed" if final_state != "handoff" else "handoff"
            run.intent = str(intent) if intent is not None else None
            run.final_state = final_state
            run.node_history = node_history
            run.latency_ms = latency_ms
            run.handoff_reason = (
                str(handoff_reason) if handoff_reason is not None else None
            )
            run.error_type = str(error_type) if error_type is not None else None
            run.finished_at = datetime.now(UTC)
            await session.flush()
            return assistant_message.id
