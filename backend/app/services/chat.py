import asyncio
from builtins import Exception
import time
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.graph import GraphDependencies, build_graph
from app.core.telemetry import (
    ModelUsage,
    add_model_usage,
    bind_run,
    consume_model_usage,
    record_node_span,
)
from app.db.models import AgentRun, BadCase, Conversation, Message
from app.llm.providers import ChatProvider, ChatResult
from app.repositories.agent_runs import AgentRunRepository
from app.repositories.ticket_context import load_ticket_context
from app.repositories.ticket_intake_context import load_ticket_intake_context
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


class ObservableChatProvider:
    def __init__(self, provider: ChatProvider) -> None:
        self._provider = provider

    async def complete(self, prompt: str) -> ChatResult:
        result = await self._provider.complete(prompt)
        add_model_usage(
            model=result.model,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            usage_uncertain=result.usage_uncertain,
        )
        return result


class ChatService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        graph_dependencies: GraphDependencies,
        ticket_service: TicketService,
    ) -> None:
        self._session_factory = session_factory
        self._graph_dependencies = GraphDependencies(
            retriever=graph_dependencies.retriever,
            chat_provider=ObservableChatProvider(graph_dependencies.chat_provider),
            ticket_service=graph_dependencies.ticket_service,
            user_access_level=graph_dependencies.user_access_level,
            minimum_evidence_score=graph_dependencies.minimum_evidence_score,
            require_structured_citations=graph_dependencies.require_structured_citations,
        )
        self._ticket_service = ticket_service
        self._runs = AgentRunRepository(session_factory)

    async def stream_run(self, *, user_id: str, conversation_id: str, content: str,
                         trace_id: str, run_id: str, history: list[dict[str, str]], lease_token: str | None = None,
                         ticket_context: dict[str, Any] | None = None,
                         ticket_intake_context: dict[str, Any] | None = None) -> AsyncIterator[ChatEvent]:
        """Execute a durable job; the caller owns all business persistence and leases."""
        state: dict[str, Any] = {"user_id":user_id, "conversation_id":conversation_id,
                                 "message":content, "step_count":0, "trace_id":trace_id,
                                 "history":history,"run_id":run_id,"lease_token":lease_token}
        if ticket_context is not None:
            state["ticket_context"] = ticket_context
        if ticket_intake_context is not None:
            state["ticket_intake_context"] = ticket_intake_context
        total_input = total_output = 0
        uncertain = False
        model: str | None = None
        with bind_run(trace_id, run_id):
            yield ChatEvent("run_started", {"run_id":run_id})
            graph = build_graph(self._graph_dependencies)
            node_started_ns = time.time_ns()
            node_started = time.perf_counter()
            async for updates in graph.astream(state, stream_mode="updates"):
                for node, update in updates.items():
                    state.update(update)
                    usage = consume_model_usage()
                    total_input += usage.input_tokens
                    total_output += usage.output_tokens
                    uncertain = uncertain or usage.usage_uncertain
                    model = usage.model or model
                    latency_ms=int((time.perf_counter()-node_started)*1000)
                    record_node_span(node=node, started_ns=node_started_ns,
                        latency_ms=latency_ms,
                        result_category="error" if update.get("error") else str(update.get("final_state") or "completed"),
                        usage=usage)
                    node_started_ns, node_started = time.time_ns(), time.perf_counter()
                    yield ChatEvent("node_completed", {"node":node,"step_count":state.get("step_count",0),"latency_ms":latency_ms})
            citations = [asdict(c) for c in state.get("citations", [])]
            final_state = state.get("final_state", "handoff")
            if final_state != "awaiting_confirmation":
                state.pop("ticket_draft", None)
                state.pop("confirmation_token", None)
            knowledge_context = {"knowledge_context": state["knowledge_context"]} if "knowledge_context" in state else {}
            yield ChatEvent("citations", {"citations":citations, **knowledge_context})
            draft = state.get("ticket_draft")
            if isinstance(draft, TicketDraft) and state.get("confirmation_token"):
                yield ChatEvent("ticket_draft", {"draft":draft.model_dump(mode="json"),"confirmation_token":state["confirmation_token"]})
            if state.get("handoff_reason"):
                yield ChatEvent("handoff", {"reason":state["handoff_reason"]})
            yield ChatEvent("final", {"run_id":run_id,"answer":state.get("answer","处理失败，请联系IT支持。"),
                                       "final_state":final_state,"citations":citations,
                                       **knowledge_context,
                                       "handoff_reason":state.get("handoff_reason"),
                                       "error":state.get("error"),
                                       **({"ticket_lookup": state["ticket_lookup"]} if "ticket_lookup" in state else {}),
                                       **({"ticket_intake": state["ticket_intake"]} if "ticket_intake" in state else {}),
                                       "usage":{"input_tokens":total_input,"output_tokens":total_output,"model":model,"usage_uncertain":uncertain}})

    async def stream_message(
        self,
        *,
        user_id: str,
        conversation_id: str,
        content: str,
        trace_id: str,
    ) -> AsyncIterator[ChatEvent]:
        await self._require_conversation_owner(user_id, conversation_id)
        started = await self._runs.start(
            conversation_id=conversation_id,
            content=content,
            trace_id=trace_id,
        )
        message_id, run_id = started.message_id, started.run_id
        started_at = time.perf_counter()
        node_history: list[dict[str, Any]] = []
        state: dict[str, Any] = {
            "user_id": user_id,
            "conversation_id": conversation_id,
            "message": content,
            "step_count": 0,
            "trace_id": trace_id,
        }
        finalized = False
        try:
            yield ChatEvent(
                "run_started", {"run_id": run_id, "message_id": message_id}
            )
            with bind_run(trace_id, run_id):
                async for event in self._process_run(
                    run_id=run_id,
                    conversation_id=conversation_id,
                    state=state,
                    node_history=node_history,
                    started_at=started_at,
                ):
                    yield event
                finalized = True
                async for event in self._terminal_events(run_id, state):
                    yield event
        finally:
            if not finalized:
                await self._reliably_mark_run_handoff(
                    run_id=run_id,
                    node_history=node_history,
                    latency_ms=int((time.perf_counter() - started_at) * 1000),
                )

    async def _process_run(
        self,
        *,
        run_id: str,
        conversation_id: str,
        state: dict[str, Any],
        node_history: list[dict[str, Any]],
        started_at: float,
    ) -> AsyncIterator[ChatEvent]:
        try:
            try:
                async with self._session_factory() as session:
                    state["ticket_context"] = await load_ticket_context(session, state["user_id"], conversation_id,
                                                                        run_id, production=False)
                    state["ticket_intake_context"] = await load_ticket_intake_context(session, state["user_id"], conversation_id,
                                                                                      run_id, production=False)
                graph = build_graph(self._graph_dependencies)
                async for updates in graph.astream(state, stream_mode="updates"):
                    for node_name, update in updates.items():
                        node_finished = time.perf_counter()
                        state.update(update)
                        latency_ms = max(0, int((node_finished - started_at) * 1000) - sum(
                            int(item["latency_ms"]) for item in node_history
                        ))
                        usage = consume_model_usage()
                        result_category = self._node_result_category(update)
                        document_ids = self._document_ids(update)
                        ticket_number = update.get("ticket_number")
                        node_history.append(
                            {
                                "node": node_name,
                                "latency_ms": latency_ms,
                                "result_category": result_category,
                                "model": usage.model,
                                "input_tokens": usage.input_tokens,
                                "output_tokens": usage.output_tokens,
                                **({"ticket_lookup": update["ticket_lookup"]} if "ticket_lookup" in update else {}),
                                **({"ticket_intake": update["ticket_intake"]} if "ticket_intake" in update else {}),
                            }
                        )
                        record_node_span(
                            node=node_name,
                            started_ns=time.time_ns() - latency_ms * 1_000_000,
                            latency_ms=latency_ms,
                            result_category=result_category,
                            document_ids=document_ids,
                            ticket_number=str(ticket_number) if ticket_number else None,
                            usage=usage,
                        )
                        yield ChatEvent(
                            "node_completed",
                            {"node": node_name, "step_count": state.get("step_count", 0)},
                        )
            except asyncio.CancelledError:
                raise
            except Exception:
                self._set_handoff(state, "processing_failed")

            citations = [
                asdict(citation) for citation in state.get("citations", [])
            ]
            total_usage = self._total_usage(node_history)
            draft = state.get("ticket_draft")
            confirmation_token = state.get("confirmation_token")
            handoff_reason = state.get("handoff_reason")
            final_state = str(state.get("final_state", "handoff"))
            answer = str(
                state.get(
                    "answer",
                    "The request could not be completed. Please contact IT support.",
                )
            )
            assistant_message_id: str | None
            try:
                assistant_message_id = await self._runs.finish(
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
                    model_name=total_usage.model,
                    input_tokens=total_usage.input_tokens,
                    output_tokens=total_usage.output_tokens,
                )
                finalized = True
            except Exception:
                self._set_handoff(state, "persistence_failed")
                draft = None
                confirmation_token = None
                final_state = "handoff"
                handoff_reason = "persistence_failed"
                answer = str(state["answer"])
                assistant_message_id = None
                await self._reliably_mark_run_handoff(
                    run_id=run_id,
                    node_history=node_history,
                    latency_ms=int((time.perf_counter() - started_at) * 1000),
                )
            state["_assistant_message_id"] = assistant_message_id
            state["answer"] = answer
            state["final_state"] = final_state
            state["handoff_reason"] = handoff_reason
            state["citations"] = citations
            state["ticket_draft"] = draft
            state["confirmation_token"] = confirmation_token
        except asyncio.CancelledError:
            raise

    @staticmethod
    async def _terminal_events(
        run_id: str, state: dict[str, Any]
    ) -> AsyncIterator[ChatEvent]:
        citations = state.get("citations", [])
        draft = state.get("ticket_draft")
        confirmation_token = state.get("confirmation_token")
        handoff_reason = state.get("handoff_reason")
        yield ChatEvent("citations", {"citations": citations})
        if isinstance(draft, TicketDraft) and isinstance(confirmation_token, str):
            yield ChatEvent(
                "ticket_draft",
                {
                    "draft": draft.model_dump(mode="json"),
                    "confirmation_token": confirmation_token,
                },
            )
        if isinstance(handoff_reason, str) and handoff_reason:
            yield ChatEvent("handoff", {"reason": handoff_reason})
        yield ChatEvent(
            "final",
            {
                "run_id": run_id,
                "message_id": state.get("_assistant_message_id"),
                "answer": state["answer"],
                "final_state": state["final_state"],
                **({"ticket_lookup": state["ticket_lookup"]} if "ticket_lookup" in state else {}),
                **({"ticket_intake": state["ticket_intake"]} if "ticket_intake" in state else {}),
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
        trace_id: str | None = None,
    ) -> dict[str, str]:
        await self._require_conversation_owner(user_id, conversation_id)
        result = await self._ticket_service.create_confirmed(
            user_id=user_id,
            draft=draft,
            confirmation_token=confirmation_token,
            idempotency_key=idempotency_key,
            conversation_id=conversation_id,
            request_trace_id=trace_id,
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
        user_id: str,
        feedback: str,
    ) -> None:
        async with self._session_factory.begin() as session:
            message = await session.scalar(
                select(Message)
                .join(Conversation, Message.conversation_id == Conversation.id)
                .where(Message.id == message_id, Conversation.user_id == user_id)
            )
            if message is None:
                raise MessageNotFoundError
            message.user_feedback = {"feedback": feedback}
            if feedback == "unresolved":
                run = await session.scalar(
                    select(AgentRun).where(AgentRun.result_message_id == message.id)
                )
                if run is not None:
                    citation_ids = sorted(
                        {
                            str(citation["document_id"])
                            for citation in message.citations
                            if citation.get("document_id")
                        }
                    )
                    await session.execute(
                        insert(BadCase)
                        .values(
                            assistant_message_id=message.id,
                            agent_run_id=run.id,
                            trace_id=run.trace_id,
                            final_state=run.final_state,
                            citation_ids=citation_ids,
                            reason="user_unresolved",
                        )
                        .on_conflict_do_nothing(index_elements=["assistant_message_id"])
                    )

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

    async def _mark_run_handoff(
        self,
        *,
        run_id: str,
        node_history: list[dict[str, Any]],
        latency_ms: int,
    ) -> None:
        await self._runs.handoff(
            run_id=run_id,
            node_history=node_history,
            latency_ms=latency_ms,
        )

    async def _reliably_mark_run_handoff(
        self,
        *,
        run_id: str,
        node_history: list[dict[str, Any]],
        latency_ms: int,
    ) -> None:
        task = asyncio.create_task(
            self._mark_run_handoff(
                run_id=run_id,
                node_history=node_history,
                latency_ms=latency_ms,
            )
        )
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await asyncio.shield(task)
            raise

    @staticmethod
    def _set_handoff(state: dict[str, Any], reason: str) -> None:
        state.update(
            answer="The request could not be completed. Please contact IT support.",
            citations=[],
            final_state="handoff",
            handoff_reason=reason,
            error=reason,
        )

    @staticmethod
    def _node_result_category(update: dict[str, Any]) -> str:
        if update.get("error"):
            return "error"
        if update.get("final_state"):
            return str(update["final_state"])
        return "completed"

    @staticmethod
    def _document_ids(update: dict[str, Any]) -> list[str]:
        hits = update.get("retrieval_hits", [])
        return sorted(
            {
                str(hit.citation.document_id)
                for hit in hits
                if getattr(getattr(hit, "citation", None), "document_id", None)
            }
        )

    @staticmethod
    def _total_usage(node_history: list[dict[str, Any]]) -> ModelUsage:
        models = [str(item["model"]) for item in node_history if item.get("model")]
        return ModelUsage(
            model=models[-1] if models else None,
            input_tokens=sum(int(item.get("input_tokens", 0)) for item in node_history),
            output_tokens=sum(int(item.get("output_tokens", 0)) for item in node_history),
        )
