import json
import os
import asyncio
from collections.abc import Generator
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from redis.asyncio import Redis
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.agent.graph import GraphDependencies
from app.db.models import AgentRun, BadCase, Conversation, Message, Ticket, TicketEvent, ToolAudit, User
from app.llm.providers import MockChatProvider
from app.main import app
from app.rag.retriever import Citation, RetrievalHit
from app.repositories.tickets import TicketRepository
from app.services.chat import ChatService
from app.tickets.service import TicketService


@dataclass(frozen=True, slots=True)
class ChatApiContext:
    session_factory: async_sessionmaker[AsyncSession]
    baseline: dict[str, list[str]]


class StaticRetriever:
    async def retrieve(
        self,
        query: str,
        *,
        user_access_level: str,
        limit: int = 5,
    ) -> list[RetrievalHit]:
        del query, user_access_level, limit
        return [
            RetrievalHit(
                citation=Citation(
                    document_id="test-vpn-document",
                    source_title="VPN troubleshooting",
                    source_path="vpn.md",
                    chunk_index=0,
                    excerpt="Reconnect the VPN client after checking the network.",
                ),
                score=0.9,
                vector_score=0.9,
                bm25_score=1.0,
                combined_score=0.9,
            )
        ]


class BlockingGraph:
    def __init__(self, started: asyncio.Event, release: asyncio.Event) -> None:
        self._started = started
        self._release = release

    async def astream(self, state, *, stream_mode):
        del state, stream_mode
        self._started.set()
        await self._release.wait()
        yield {"classify_intent": {"intent": "knowledge", "step_count": 1}}
        yield {
            "handoff": {
                "answer": "No answer.",
                "citations": [],
                "final_state": "handoff",
                "handoff_reason": "insufficient_evidence",
                "step_count": 2,
            }
        }


@pytest.fixture
def client() -> Generator[tuple[TestClient, ChatApiContext]]:
    database_url = os.getenv(
        "TEST_DATABASE_URL",
        "postgresql+asyncpg://itops:itops-local-only@localhost:15432/itops",
    )
    redis = Redis.from_url(
        os.getenv("TEST_REDIS_URL", "redis://localhost:6379/15"),
        decode_responses=True,
    )
    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    ticket_service = TicketService(
        TicketRepository(session_factory),
        redis,
        confirmation_key_prefix="test:chat-api-confirmation",
    )
    app.state.chat_service = ChatService(
        session_factory,
        GraphDependencies(
            retriever=StaticRetriever(),
            chat_provider=MockChatProvider(),
            ticket_service=ticket_service,
        ),
        ticket_service,
    )
    baseline: dict[str, list[str]] = {}
    context = ChatApiContext(session_factory, baseline)

    async def setup() -> None:
        async with session_factory.begin() as session:
            baseline["message_ids"] = list(
                (
                    await session.scalars(
                        select(Message.id).where(Message.conversation_id == "c-001")
                    )
                ).all()
            )
            baseline["run_ids"] = list(
                (
                    await session.scalars(
                        select(AgentRun.id).where(AgentRun.conversation_id == "c-001")
                    )
                ).all()
            )
            baseline["ticket_ids"] = list(
                (await session.scalars(select(Ticket.id).where(Ticket.user_id == "u-001"))).all()
            )
            if await session.get(User, "u-001") is None:
                session.add(User(id="u-001", display_name="Chat API test user"))
            if await session.get(Conversation, "c-001") is None:
                session.add(Conversation(id="c-001", user_id="u-001"))
            if await session.get(Conversation, "c-api-owned-001") is None:
                session.add(Conversation(id="c-api-owned-001", user_id="u-001"))
            if await session.get(User, "u-002") is None:
                session.add(User(id="u-002", display_name="Other chat API test user"))
            if await session.get(Conversation, "c-other") is None:
                session.add(Conversation(id="c-other", user_id="u-002"))

    async def cleanup() -> None:
        async with session_factory.begin() as session:
            new_ticket_ids = select(Ticket.id).where(
                Ticket.user_id == "u-001",
                Ticket.id.not_in(baseline["ticket_ids"]),
            )
            await session.execute(delete(ToolAudit).where(ToolAudit.ticket_id.in_(new_ticket_ids)))
            await session.execute(delete(TicketEvent).where(TicketEvent.ticket_id.in_(new_ticket_ids)))
            await session.execute(delete(Ticket).where(Ticket.id.in_(new_ticket_ids)))
            await session.execute(
                delete(BadCase).where(BadCase.agent_run_id.in_(
                    select(AgentRun.id).where(
                        AgentRun.conversation_id == "c-001",
                        AgentRun.id.not_in(baseline["run_ids"]),
                    )
                ))
            )
            await session.execute(
                delete(AgentRun).where(
                    AgentRun.conversation_id == "c-001",
                    AgentRun.id.not_in(baseline["run_ids"]),
                )
            )
            await session.execute(
                delete(Message).where(
                    Message.conversation_id == "c-001",
                    Message.id.not_in(baseline["message_ids"]),
                )
            )

    async def teardown() -> None:
        await cleanup()
        await redis.aclose()
        await engine.dispose()

    with TestClient(app) as test_client:
        test_client.portal.call(setup)
        try:
            yield test_client, context
        finally:
            test_client.portal.call(teardown)


def _sse_payloads(response) -> list[tuple[str, dict[str, object]]]:
    lines = [line for line in response.text.splitlines() if line]
    return [
        (lines[index][7:], json.loads(lines[index + 1][6:]))
        for index in range(0, len(lines), 2)
    ]


def test_chat_stream_ends_with_final_event(client) -> None:
    test_client, context = client
    response = test_client.post(
        "/api/conversations/c-001/messages:stream",
        headers={"X-Trace-Id": "trace-chat-001"},
        json={"user_id": "u-001", "content": "VPN cannot connect"},
    )
    events = [line for line in response.text.splitlines() if line.startswith("event:")]
    payloads = _sse_payloads(response)

    assert response.status_code == 200
    assert response.headers["X-Trace-Id"] == "trace-chat-001"
    assert events[0] == "event: run_started"
    assert "event: citations" in events
    assert events[-1] == "event: final"
    assert all(payload["trace_id"] == "trace-chat-001" for _, payload in payloads)

    final = payloads[-1][1]
    async def persisted() -> tuple[int, int]:
        async with context.session_factory() as session:
            messages = await session.scalars(
                select(Message).where(Message.conversation_id == "c-001")
            )
            runs = await session.scalars(
                select(AgentRun).where(AgentRun.conversation_id == "c-001")
            )
            return len(list(messages)), len(list(runs))
    expected_counts = (
        len(context.baseline["message_ids"]) + 2,
        len(context.baseline["run_ids"]) + 1,
    )
    assert test_client.portal.call(persisted) == expected_counts
    assert isinstance(final["message_id"], str)


def test_confirm_ticket_requires_confirmation_token(client) -> None:
    test_client, _ = client
    response = test_client.post(
        "/api/conversations/c-001/ticket-confirmations",
        json={"user_id": "u-001", "draft": {}},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


def test_confirmed_draft_creates_ticket_and_feedback_persists(client) -> None:
    test_client, context = client
    stream = test_client.post(
        "/api/conversations/c-001/messages:stream",
        headers={"X-Trace-Id": "trace-confirmed-ticket"},
        json={"user_id": "u-001", "content": "Please create ticket\n问题：VPN outage\n影响范围：仅本人\n已尝试：尚未尝试"},
    )
    payloads = dict(_sse_payloads(stream))
    draft = payloads["ticket_draft"]

    confirmed = test_client.post(
        "/api/conversations/c-001/ticket-confirmations",
        headers={"X-Trace-Id": "trace-confirmed-ticket"},
        json={
            "user_id": "u-001",
            "confirmation_token": draft["confirmation_token"],
            "draft": draft["draft"],
            "idempotency_key": "chat-api-ticket-001",
        },
    )
    ticket = confirmed.json()
    long_ticket_first_number = "A" * 64
    long_ticket_number = "A" * 65
    fourth_status = test_client.get(f"/api/tickets/{long_ticket_first_number}?user_id=u-001")
    third_status = test_client.get(f"/api/tickets/{long_ticket_number}?user_id=u-001")
    second_status = test_client.get(f"/api/tickets/{ticket['ticket_number']}?user_id=u-002")
    none_status = test_client.get(f"/api/tickets/{ticket['ticket_number']}?")
    over_status = test_client.get(f"/api/tickets/{ticket['ticket_number']}?user_id=aB3kP9mX2vL8nR4tYcW6hJ7sF1dQ5gU0eI9oZxC2pV7bN4mK8qW3yH6jL1tR5sFxy")
    invalid_status = test_client.get(f"/api/tickets/{ticket['ticket_number']}?user_id=")
    status = test_client.get(f"/api/tickets/{ticket['ticket_number']}?user_id=u-001")
    assistant_message_id = payloads["final"]["message_id"]
    feedback = test_client.post(
        f"/api/messages/{assistant_message_id}/feedback?user_id=u-001",
        json={"feedback": "resolved"},
    )

    assert confirmed.status_code == 201
    assert ticket["ticket_number"].startswith("IT-")
    assert status.status_code == 200
    assert status.json()["ticket_number"] == ticket["ticket_number"]
    assert feedback.status_code == 200
    assert invalid_status.status_code == 422
    assert invalid_status.json()["code"] == "validation_error"
    assert over_status.status_code == 422
    assert over_status.json()["code"] == "validation_error"
    assert none_status.status_code == 422
    assert none_status.json()["code"] == "validation_error"
    assert second_status.status_code == 404
    assert second_status.json()["code"] == "ticket_not_found"
    assert third_status.status_code == 422
    assert third_status.json()["code"] == "validation_error"
    assert fourth_status.status_code == 404
    assert fourth_status.json()["code"] == "ticket_not_found"

    async def feedback_value() -> dict[str, str] | None:
        async with context.session_factory() as session:
            message = await session.get(Message, assistant_message_id)
            return message.user_feedback if message else None
    assert test_client.portal.call(feedback_value) == {"feedback": "resolved"}


def test_errors_echo_trace_id_without_internal_details(client) -> None:
    test_client, _ = client
    response = test_client.post(
        "/api/conversations/c-001/messages:stream",
        headers={"X-Trace-Id": "trace-validation-001"},
        json={"user_id": "u-001", "content": "", "extra": "forbidden"},
    )

    assert response.status_code == 422
    assert response.headers["X-Trace-Id"] == "trace-validation-001"
    assert response.json() == {
        "code": "validation_error",
        "message": "Request validation failed.",
        "trace_id": "trace-validation-001",
    }


def test_ticket_confirmation_rejects_extra_draft_fields(client) -> None:
    test_client, _ = client
    response = test_client.post(
        "/api/conversations/c-001/ticket-confirmations",
        json={
            "user_id": "u-001",
            "confirmation_token": "token",
            "idempotency_key": "key",
            "draft": {
                "title": "VPN",
                "category": "network",
                "priority": "medium",
                "description": "VPN cannot connect",
                "unexpected": "field",
            },
        },
    )

    assert response.status_code == 422


def test_not_found_error_uses_the_trace_error_contract(client) -> None:
    test_client, _ = client
    response = test_client.get(
        "/api/not-a-route",
        headers={"X-Trace-Id": "trace-404-001"},
    )

    assert response.status_code == 404
    assert response.headers["X-Trace-Id"] == "trace-404-001"
    assert response.json() == {
        "code": "not_found",
        "message": "Resource was not found.",
        "trace_id": "trace-404-001",
    }


def test_confirmation_token_cannot_be_reused_for_another_owned_conversation(
    client,
) -> None:
    test_client, _ = client
    stream = test_client.post(
        "/api/conversations/c-001/messages:stream",
        headers={"X-Trace-Id": "trace-conversation-swap"},
        json={"user_id": "u-001", "content": "Please create ticket\n问题：VPN outage\n影响范围：仅本人\n已尝试：尚未尝试"},
    )
    draft_event = dict(_sse_payloads(stream))["ticket_draft"]

    response = test_client.post(
        "/api/conversations/c-api-owned-001/ticket-confirmations",
        headers={"X-Trace-Id": "trace-conversation-swap"},
        json={
            "user_id": "u-001",
            "confirmation_token": draft_event["confirmation_token"],
            "draft": draft_event["draft"],
            "idempotency_key": "conversation-swap-key",
        },
    )

    assert response.status_code == 403
    assert response.json()["code"] == "confirmation_invalid"


def test_feedback_is_limited_to_the_message_owner(client) -> None:
    test_client, _ = client
    stream = test_client.post(
        "/api/conversations/c-001/messages:stream",
        json={"user_id": "u-001", "content": "VPN cannot connect"},
    )
    message_id = dict(_sse_payloads(stream))["final"]["message_id"]

    denied = test_client.post(
        f"/api/messages/{message_id}/feedback?user_id=u-002",
        json={"feedback": "resolved"},
    )
    accepted = test_client.post(
        f"/api/messages/{message_id}/feedback?user_id=u-001",
        json={"feedback": "resolved"},
    )

    assert denied.status_code == 404
    assert accepted.status_code == 200


def test_stream_failure_is_a_complete_handoff_with_final_event(client, monkeypatch) -> None:
    test_client, context = client
    service = app.state.chat_service
    del context, service

    def fail_to_build_graph(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("internal graph secret")

    monkeypatch.setattr("app.services.chat.build_graph", fail_to_build_graph)
    response = test_client.post(
        "/api/conversations/c-001/messages:stream",
        headers={"X-Trace-Id": "trace-stream-failure"},
        json={"user_id": "u-001", "content": "VPN cannot connect"},
    )
    events = _sse_payloads(response)

    assert response.status_code == 200
    assert events[-1][0] == "final"
    assert "handoff" in [name for name, _ in events]
    assert all(data["trace_id"] == "trace-stream-failure" for _, data in events)
    assert "internal graph secret" not in response.text


def test_service_cancellation_finalizes_an_existing_run(client) -> None:
    test_client, context = client
    service = app.state.chat_service

    async def cancel_after_start() -> None:
        events = service.stream_message(
            user_id="u-001",
            conversation_id="c-001",
            content="VPN cannot connect",
            trace_id="trace-cancelled",
        )
        await anext(events)
        await events.aclose()

    test_client.portal.call(cancel_after_start)

    async def latest_run_status() -> str:
        async with context.session_factory() as session:
            run = await session.scalar(
                select(AgentRun)
                .where(AgentRun.trace_id == "trace-cancelled")
                .order_by(AgentRun.created_at.desc())
            )
            return run.status

    assert test_client.portal.call(latest_run_status) != "running"


def test_feedback_requires_the_owner_user_id_query_parameter(client) -> None:
    test_client, _ = client
    response = test_client.post(
        "/api/messages/message-id/feedback",
        json={"feedback": "resolved"},
    )

    assert response.status_code == 422


def test_run_started_is_available_before_the_graph_finishes(client, monkeypatch) -> None:
    test_client, _ = client

    async def exercise() -> list[str]:
        started = asyncio.Event()
        release = asyncio.Event()
        monkeypatch.setattr(
            "app.services.chat.build_graph",
            lambda dependencies: BlockingGraph(started, release),
        )
        events = app.state.chat_service.stream_message(
            user_id="u-001",
            conversation_id="c-001",
            content="VPN cannot connect",
            trace_id="trace-realtime-001",
        )
        first = await asyncio.wait_for(anext(events), timeout=0.1)
        assert first.name == "run_started"
        assert not started.is_set()
        next_event = asyncio.create_task(anext(events))
        await started.wait()
        assert not next_event.done()
        release.set()
        names = [first.name, (await next_event).name]
        async for event in events:
            names.append(event.name)
        return names

    names = test_client.portal.call(exercise)

    assert names[0] == "run_started"
    assert "node_completed" in names
    assert names[-1] == "final"
