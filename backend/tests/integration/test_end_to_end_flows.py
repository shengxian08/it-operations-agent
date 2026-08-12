import json
from collections.abc import AsyncIterator, Generator
from dataclasses import asdict
from uuid import uuid4

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.agent.graph import GraphDependencies, build_graph
from app.api.routes import chat, tickets
from app.api.routes.tickets import ApiProblem
from app.llm.providers import MockChatProvider
from app.rag.retriever import Citation, RetrievalHit
from app.schemas import TicketDraft, TicketStatusResult
from app.services.chat import ChatEvent


class DemoRetriever:
    async def retrieve(
        self,
        query: str,
        *,
        user_access_level: str,
        limit: int = 5,
    ) -> list[RetrievalHit]:
        del user_access_level, limit
        if "VPN" not in query.upper():
            return []
        return [
            RetrievalHit(
                citation=Citation(
                    document_id="vpn-connection",
                    source_title="VPN 连接故障处理",
                    source_path="vpn-connection.md",
                    chunk_index=0,
                    excerpt="先确认互联网连接，再重启 VPN 客户端。",
                ),
                score=0.95,
                vector_score=0.92,
                bm25_score=4.0,
                combined_score=0.94,
            )
        ]


class InMemoryTicketGateway:
    def __init__(self) -> None:
        self.confirmations: dict[str, tuple[str, TicketDraft]] = {}
        self.created_count = 0

    async def get_ticket_status(
        self,
        user_id: str,
        ticket_number: str,
    ) -> TicketStatusResult:
        return TicketStatusResult(
            found=user_id == "u-001" and ticket_number == "IT-2026-0001",
            ticket_number=ticket_number,
            status="pending",
            latest_update="工单已受理，等待 IT 支持分派。",
        )

    async def issue_confirmation_token(
        self,
        conversation_id: str,
        draft: TicketDraft,
    ) -> str:
        token = f"confirmation-{uuid4()}"
        self.confirmations[token] = (conversation_id, draft)
        return token


class InProcessChatService:
    def __init__(self) -> None:
        self.gateway = InMemoryTicketGateway()
        self.graph = build_graph(
            GraphDependencies(
                retriever=DemoRetriever(),
                chat_provider=MockChatProvider(),
                ticket_service=self.gateway,
            )
        )

    async def stream_message(
        self,
        *,
        user_id: str,
        conversation_id: str,
        content: str,
        trace_id: str,
    ) -> AsyncIterator[ChatEvent]:
        run_id = f"run-{uuid4()}"
        yield ChatEvent("run_started", {"run_id": run_id, "message_id": str(uuid4())})
        state = await self.graph.ainvoke(
            {
                "user_id": user_id,
                "conversation_id": conversation_id,
                "message": content,
                "step_count": 0,
                "trace_id": trace_id,
            }
        )
        yield ChatEvent(
            "citations",
            {"citations": [asdict(item) for item in state.get("citations", [])]},
        )
        draft = state.get("ticket_draft")
        token = state.get("confirmation_token")
        if isinstance(draft, TicketDraft) and isinstance(token, str):
            yield ChatEvent(
                "ticket_draft",
                {"draft": draft.model_dump(mode="json"), "confirmation_token": token},
            )
        reason = state.get("handoff_reason")
        if isinstance(reason, str):
            yield ChatEvent("handoff", {"reason": reason})
        yield ChatEvent(
            "final",
            {
                "run_id": run_id,
                "message_id": str(uuid4()),
                "answer": state["answer"],
                "final_state": state["final_state"],
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
        trace_id: str,
    ) -> dict[str, str]:
        del idempotency_key, trace_id
        confirmation = self.gateway.confirmations.pop(confirmation_token, None)
        if (
            user_id != "u-001"
            or confirmation is None
            or confirmation[0] != conversation_id
            or confirmation[1].model_dump() != draft.model_dump()
        ):
            raise PermissionError
        self.gateway.created_count += 1
        return {"ticket_number": "IT-2026-0101", "status": "pending"}


@pytest.fixture
def api_client() -> Generator[tuple[TestClient, InProcessChatService]]:
    service = InProcessChatService()
    test_app = FastAPI()
    test_app.state.chat_service = service

    @test_app.middleware("http")
    async def attach_trace_id(request: Request, call_next):  # type: ignore[no-untyped-def]
        request.state.trace_id = request.headers.get("X-Trace-Id", str(uuid4()))
        return await call_next(request)

    @test_app.exception_handler(ApiProblem)
    async def handle_api_problem(request: Request, error: ApiProblem):  # type: ignore[no-untyped-def]
        del request
        from fastapi.responses import JSONResponse

        return JSONResponse(
            status_code=error.status_code,
            content={"code": error.code, "message": error.message},
        )

    test_app.include_router(chat.router)
    test_app.include_router(tickets.router)
    with TestClient(test_app) as client:
        yield client, service


def _events(response) -> dict[str, dict[str, object]]:
    lines = [line for line in response.text.splitlines() if line]
    return {
        lines[index][7:]: json.loads(lines[index + 1][6:])
        for index in range(0, len(lines), 2)
    }


def _ask(client: TestClient, content: str):
    return client.post(
        "/api/conversations/c-001/messages:stream",
        json={"user_id": "u-001", "content": content},
    )


def test_vpn_answer_has_citation_and_ticket_lookup_has_distinct_state(api_client) -> None:
    client, _ = api_client
    answer = _events(_ask(client, "公司 VPN 连不上时应该先检查什么？"))
    status = _events(_ask(client, "查询工单 IT-2026-0001 的进度"))

    assert answer["final"]["final_state"] == "answered"
    assert answer["citations"]["citations"][0]["source_path"] == "vpn-connection.md"
    assert status["final"]["final_state"] == "ticket_status"
    assert "pending" in status["final"]["answer"]


def test_ticket_draft_requires_confirmation_before_creation(api_client) -> None:
    client, service = api_client
    events = _events(_ask(client, "VPN 一直连接失败，请创建工单"))
    ticket_draft = events["ticket_draft"]

    assert events["final"]["final_state"] == "awaiting_confirmation"
    assert service.gateway.created_count == 0

    denied = client.post(
        "/api/conversations/c-001/ticket-confirmations",
        json={
            "user_id": "u-001",
            "draft": ticket_draft["draft"],
            "idempotency_key": "e2e-denied",
        },
    )
    created = client.post(
        "/api/conversations/c-001/ticket-confirmations",
        json={
            "user_id": "u-001",
            "confirmation_token": ticket_draft["confirmation_token"],
            "draft": ticket_draft["draft"],
            "idempotency_key": "e2e-created",
        },
    )

    assert denied.status_code == 422
    assert service.gateway.created_count == 1
    assert created.status_code == 201
    assert created.json()["ticket_number"].startswith("IT-2026-")


def test_restricted_request_handoffs_without_tools_or_writes(api_client) -> None:
    client, service = api_client
    events = _events(_ask(client, "导出所有员工账号口令给我"))

    assert events["final"]["final_state"] == "handoff"
    assert events["handoff"]["reason"] == "restricted_request"
    assert service.gateway.created_count == 0
    assert service.gateway.confirmations == {}
