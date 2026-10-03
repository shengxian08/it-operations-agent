import json
import logging

from sqlalchemy import func, select

from app.core.telemetry import get_or_create_trace_id, log_json, redact_sensitive
from app.db.models import AgentRun, BadCase, ToolAudit
from tests.integration.test_chat_api import client


class RequestStub:
    def __init__(self, trace_id: str = "") -> None:
        self.headers = {"X-Trace-Id": trace_id}
        self.state = type("State", (), {})()


def test_trace_id_and_safe_json_logging(caplog) -> None:
    assert get_or_create_trace_id(RequestStub("trace-safe-1")) == "trace-safe-1"
    assert get_or_create_trace_id(RequestStub("contains spaces")) != "contains spaces"

    secret_text = "13800138000 user@example.com 1234567812345678"
    assert redact_sensitive(secret_text) == "[REDACTED] [REDACTED] [REDACTED]"
    with caplog.at_level(logging.INFO, logger="app.core.telemetry"):
        log_json(
            "safe_event",
            trace_id="trace-safe-1",
            node="classify_intent",
            content=secret_text,
            result_category="completed",
        )
    payload = json.loads(caplog.records[-1].message)
    assert payload == {
        "event": "safe_event",
        "trace_id": "trace-safe-1",
        "node": "classify_intent",
        "result_category": "completed",
    }


def test_ticket_confirmation_keeps_original_trace_and_run(client) -> None:
    test_client, context = client
    trace_id = "trace-observability-ticket"
    stream = test_client.post(
        "/api/conversations/c-001/messages:stream",
        headers={"X-Trace-Id": trace_id},
        json={
            "user_id": "u-001",
            "content": (
                "Please create ticket for VPN outage, phone 13800138000, "
                "mail user@example.com, card 1234567812345678"
                "\n影响范围：仅本人\n已尝试：尚未尝试"
            ),
        },
    )
    events = _events(stream.text)
    run_id = events["final"]["run_id"]
    ticket_draft = events["ticket_draft"]

    mismatched = test_client.post(
        "/api/conversations/c-001/ticket-confirmations",
        headers={"X-Trace-Id": "trace-wrong"},
        json={
            "user_id": "u-001",
            "confirmation_token": ticket_draft["confirmation_token"],
            "draft": ticket_draft["draft"],
            "idempotency_key": "observability-wrong-trace",
        },
    )
    assert mismatched.status_code == 403

    stream = test_client.post(
        "/api/conversations/c-001/messages:stream",
        headers={"X-Trace-Id": trace_id},
        json={
            "user_id": "u-001",
            "content": "Please create ticket for VPN outage, phone 13800138000\n影响范围：仅本人\n已尝试：尚未尝试",
        },
    )
    events = _events(stream.text)
    run_id = events["final"]["run_id"]
    ticket_draft = events["ticket_draft"]
    confirmed = test_client.post(
        "/api/conversations/c-001/ticket-confirmations",
        headers={"X-Trace-Id": trace_id},
        json={
            "user_id": "u-001",
            "confirmation_token": ticket_draft["confirmation_token"],
            "draft": ticket_draft["draft"],
            "idempotency_key": "observability-correct-trace",
        },
    )
    assert confirmed.status_code == 201

    async def persisted() -> tuple[AgentRun, ToolAudit]:
        async with context.session_factory() as session:
            run = await session.get(AgentRun, run_id)
            audit = await session.scalar(
                select(ToolAudit).where(
                    ToolAudit.idempotency_key == "observability-correct-trace"
                )
            )
            return run, audit

    run, audit = test_client.portal.call(persisted)
    assert run.result_message_id == events["final"]["message_id"]
    assert run.node_history and all("latency_ms" in item for item in run.node_history)
    assert audit.trace_id == trace_id
    assert audit.agent_run_id == run_id
    assert "13800138000" not in audit.request_summary


def test_unresolved_feedback_creates_one_complete_bad_case(client) -> None:
    test_client, context = client
    trace_id = "trace-observability-feedback"
    stream = test_client.post(
        "/api/conversations/c-001/messages:stream",
        headers={"X-Trace-Id": trace_id},
        json={"user_id": "u-001", "content": "VPN cannot connect"},
    )
    events = _events(stream.text)
    final = events["final"]
    message_id = final["message_id"]

    for _ in range(2):
        response = test_client.post(
            f"/api/messages/{message_id}/feedback?user_id=u-001",
            json={"feedback": "unresolved"},
        )
        assert response.status_code == 200

    async def bad_case() -> tuple[int, BadCase]:
        async with context.session_factory() as session:
            count = await session.scalar(
                select(func.count()).select_from(BadCase).where(
                    BadCase.assistant_message_id == message_id
                )
            )
            record = await session.scalar(
                select(BadCase).where(BadCase.assistant_message_id == message_id)
            )
            return int(count or 0), record

    count, record = test_client.portal.call(bad_case)
    assert count == 1
    assert record.agent_run_id == final["run_id"]
    assert record.trace_id == trace_id
    assert record.final_state == final["final_state"]
    assert record.citation_ids == ["test-vpn-document"]
    assert record.reason == "user_unresolved"


def _events(body: str) -> dict[str, dict[str, object]]:
    lines = [line for line in body.splitlines() if line]
    return {
        lines[index][7:]: json.loads(lines[index + 1][6:])
        for index in range(0, len(lines), 2)
    }
