import asyncio
import importlib.util
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException
from redis.asyncio import Redis
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import Base, Conversation, Message, User


def run_module():
    try:
        available = importlib.util.find_spec("app.production.runs")
    except ModuleNotFoundError:
        available = None
    assert available, "persistent run service is missing"
    from app.production import runs

    return runs


def run_settings(**changes):
    values = dict(
        user_rate_limit=100,
        worker_concurrency=10,
        run_timeout_seconds=120,
        lease_seconds=30,
        model_monthly_budget=100,
        model_input_price=1,
        model_output_price=2,
        model_max_output_tokens=1500,
        budget_max_input_tokens=16000,
        model_max_retries=0,
        queue_limit=50,
        environment="test",
    )
    return SimpleNamespace(**(values | changes))


@pytest_asyncio.fixture
async def ctx():
    module = run_module()
    from app.production import run_models
    from app.production import knowledge_models
    from app.production import business_models
    from app.production.identity_models import IdentityAccount

    schema = "run_test_" + uuid4().hex
    admin_engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    async with admin_engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        os.environ["TEST_DATABASE_URL"],
        connect_args={"server_settings": {"search_path": schema}},
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    redis = Redis.from_url(os.environ["TEST_REDIS_URL"], decode_responses=True)
    # Only add owned tables, never drop shared database/schema.
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    owner, other = "run-owner-" + uuid4().hex, "run-other-" + uuid4().hex
    async with factory.begin() as session:
        session.add_all(
            [User(id=owner, display_name="Owner"), User(id=other, display_name="Other")]
        )
        await session.flush()
        session.add_all(
            [
                IdentityAccount(
                    user_id=owner, issuer="https://test-identity", subject=owner
                ),
                IdentityAccount(
                    user_id=other, issuer="https://test-identity", subject=other
                ),
            ]
        )
    service = module.RunService(run_settings(), factory, redis)
    principal = SimpleNamespace(user_id=owner, role="employee")
    conversation = await service.create_conversation(principal, "Test")
    yield SimpleNamespace(
        service=service,
        principal=principal,
        other=SimpleNamespace(user_id=other, role="employee"),
        conversation=conversation["id"],
        factory=factory,
        models=run_models,
    )
    await redis.aclose()
    await engine.dispose()
    async with admin_engine.begin() as connection:
        await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    await admin_engine.dispose()


@pytest.mark.asyncio
async def test_run_service_is_available():
    assert hasattr(run_module(), "RunService")


@pytest.mark.asyncio
async def test_task_mode_is_durable_and_cannot_change_on_idempotent_replay(ctx):
    key, client_id = uuid4().hex, uuid4().hex
    first = await ctx.service.enqueue(
        ctx.principal,
        ctx.conversation,
        "Investigate checkout",
        client_id,
        key,
        task_mode="investigate",
    )
    assert first["task_mode"] == "investigate"
    async with ctx.factory() as session:
        row = await session.get(ctx.models.ProductionRun, first["id"])
        assert row.agent_state["goal"] == "Investigate checkout"
        assert row.agent_state["stage"] == "queued"
    with pytest.raises(HTTPException) as error:
        await ctx.service.enqueue(
            ctx.principal, ctx.conversation, "Investigate checkout", client_id, key
        )
    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_investigation_reserves_whole_parent_budget_and_persists_final_progress(
    ctx,
):
    from decimal import Decimal

    ctx.service.settings = run_settings(agent_token_limit=48000)
    first = await ctx.service.enqueue(
        ctx.principal,
        ctx.conversation,
        "checkout investigation",
        uuid4().hex,
        uuid4().hex,
        task_mode="investigate",
    )
    async with ctx.factory() as session:
        row = await session.get(ctx.models.ProductionRun, first["id"])
        assert row.reserved_cost == Decimal("0.09600000")
    job = await ctx.service.claim("synthetic-worker")
    assert await ctx.service.append_event(
        job["id"],
        job["lease_token"],
        "agent_progress",
        {
            "stage": "observing",
            "observation": {"id": "ev"},
            "budget": {"tool_calls": 1},
        },
    )
    assert not await ctx.service.append_event(
        job["id"], "expired", "agent_progress", {"stage": "fake"}
    )
    assert await ctx.service.finalize(
        job["id"],
        job["lease_token"],
        "completed",
        {
            "answer": "readonly",
            "final_state": "investigated",
            "investigation_report": {"observations": []},
            "agent_budget": {"tool_calls": 1},
        },
    )
    async with ctx.factory() as session:
        row = await session.get(ctx.models.ProductionRun, first["id"])
        assert row.agent_state["stage"] == "completed"
        assert row.agent_state["failure_at"] is None


@pytest.mark.asyncio
async def test_concurrent_idempotent_creation_saves_one_message_and_rejects_changed_payload(
    ctx,
):
    key, client_id = uuid4().hex, uuid4().hex
    results = await asyncio.gather(
        *[
            ctx.service.enqueue(
                ctx.principal, ctx.conversation, "VPN无法连接", client_id, key
            )
            for _ in range(6)
        ]
    )
    assert len({item["id"] for item in results}) == 1
    async with ctx.factory() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(Message)
                .where(Message.conversation_id == ctx.conversation)
            )
            == 1
        )
    with pytest.raises(HTTPException) as error:
        await ctx.service.enqueue(
            ctx.principal, ctx.conversation, "changed", client_id, key
        )
    assert error.value.status_code == 409
    with pytest.raises(HTTPException) as error:
        await ctx.service.enqueue(
            ctx.principal, ctx.conversation, "second", uuid4().hex, uuid4().hex
        )
    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_runs_and_history_are_scoped_to_conversation_owner(ctx):
    run = await ctx.service.enqueue(
        ctx.principal, ctx.conversation, "help", uuid4().hex, uuid4().hex
    )
    for operation in [
        ctx.service.get_run(ctx.other, run["id"]),
        ctx.service.messages(ctx.other, ctx.conversation),
        ctx.service.enqueue(
            ctx.other, ctx.conversation, "bad", uuid4().hex, uuid4().hex
        ),
    ]:
        with pytest.raises(HTTPException) as error:
            await operation
        assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_lease_fencing_and_event_replay_survive_http_disconnect(ctx):
    from app.production.answer_access import KNOWLEDGE_HISTORY
    from tests.production.test_answer_replay import knowledge, provenance

    _, citations = await knowledge(ctx)
    run = await ctx.service.enqueue(
        ctx.principal, ctx.conversation, "first turn", uuid4().hex, uuid4().hex
    )
    claimed = await ctx.service.claim("test-worker")
    assert claimed["id"] == run["id"]
    lease = claimed["lease_token"]
    assert not await ctx.service.append_event(
        run["id"], "wrong-token", "node_completed", {"node": "answer"}
    )
    assert await ctx.service.append_event(
        run["id"], lease, "node_completed", {"node": "answer"}
    )
    assert await ctx.service.finalize(
        run["id"],
        lease,
        "completed",
        {
            "answer": "saved answer",
            "final_state": "answered",
            "citations": citations,
            "knowledge_context": provenance(citations),
        },
        usage={"input_tokens": 10, "output_tokens": 3},
    )
    final = await ctx.service.get_run(ctx.principal, run["id"])
    assert final["status"] == "completed"
    assert all(
        event["data"].get("trace_id") == run["trace_id"] for event in final["events"]
    )
    sequences = [event["sequence"] for event in final["events"]]
    assert sequences == list(range(1, len(sequences) + 1))
    replay = await ctx.service.events(ctx.principal, run["id"], after=sequences[0])
    assert replay[-1]["type"] == "final"
    assert replay[-1]["data"]["answer"] == "saved answer"
    assert not await ctx.service.finalize(
        run["id"], lease, "completed", {"answer": "late write"}
    )
    second = await ctx.service.enqueue(
        ctx.principal, ctx.conversation, "second turn", uuid4().hex, uuid4().hex
    )
    history = await ctx.service.history(second["id"])
    assert [(item["role"], item["content"]) for item in history] == [
        ("user", "first turn"),
        ("assistant", KNOWLEDGE_HISTORY),
    ]
    assert (await ctx.service.get_run(ctx.principal, run["id"]))["result"][
        "answer"
    ] == "saved answer"


@pytest.mark.asyncio
async def test_cancel_and_reap_produce_durable_terminal_state_and_reject_stale_worker(
    ctx,
):
    run = await ctx.service.enqueue(
        ctx.principal, ctx.conversation, "help", uuid4().hex, uuid4().hex
    )
    claimed = await ctx.service.claim("worker")
    await ctx.service.cancel(ctx.principal, run["id"])
    assert not await ctx.service.heartbeat(run["id"], claimed["lease_token"])
    assert not await ctx.service.finalize(
        run["id"], claimed["lease_token"], "completed", {"answer": "stale"}
    )
    assert (await ctx.service.get_run(ctx.principal, run["id"]))[
        "status"
    ] == "cancelled"
    next_run = await ctx.service.enqueue(
        ctx.principal, ctx.conversation, "next", uuid4().hex, uuid4().hex
    )
    claimed = await ctx.service.claim("worker")
    async with ctx.factory.begin() as session:
        await session.execute(
            update(ctx.models.ProductionRun)
            .where(ctx.models.ProductionRun.id == next_run["id"])
            .values(
                lease_expires_at=func.now()
                - __import__("datetime").timedelta(seconds=5)
            )
        )
    assert await ctx.service.reap() >= 1
    reaped = await ctx.service.get_run(ctx.principal, next_run["id"])
    assert reaped["status"] == "failed"
    assert reaped["result"].get("escalation_id"), (
        "crashed workers must leave a durable support escalation"
    )
    assert not await ctx.service.append_event(
        next_run["id"], claimed["lease_token"], "node_completed", {}
    )


@pytest.mark.asyncio
async def test_budget_reserves_retry_upper_bound_atomically_and_refunds_never_started_run(
    ctx,
):
    from decimal import Decimal

    ctx.service.settings = run_settings(
        model_max_retries=1, model_monthly_budget=Decimal("0.04")
    )
    conversations = [
        await ctx.service.create_conversation(ctx.principal, str(index))
        for index in range(4)
    ]
    results = await asyncio.gather(
        *[
            ctx.service.enqueue(
                ctx.principal, row["id"], "hello", uuid4().hex, uuid4().hex
            )
            for row in conversations
        ],
        return_exceptions=True,
    )
    accepted = [result for result in results if isinstance(result, dict)]
    assert len(accepted) == 1
    assert all(
        result.status_code == 429
        for result in results
        if isinstance(result, HTTPException)
    )
    async with ctx.factory() as session:
        budget = await session.get(
            ctx.models.ModelBudget,
            __import__("datetime")
            .datetime.now(__import__("datetime").timezone.utc)
            .strftime("%Y-%m"),
        )
        assert budget.reserved == Decimal("0.038")
    await ctx.service.cancel(ctx.principal, accepted[0]["id"])
    async with ctx.factory() as session:
        budget = await session.get(
            ctx.models.ModelBudget,
            __import__("datetime")
            .datetime.now(__import__("datetime").timezone.utc)
            .strftime("%Y-%m"),
        )
        assert budget.reserved == 0 and budget.spent == 0


@pytest.mark.asyncio
async def test_global_queue_limit_rejects_admission_and_does_not_save_rejected_message(
    ctx,
):
    ctx.service.settings = run_settings(queue_limit=1)
    await ctx.service.enqueue(
        ctx.principal, ctx.conversation, "accepted", uuid4().hex, uuid4().hex
    )
    other_conversation = await ctx.service.create_conversation(ctx.principal, "Second")
    with pytest.raises(HTTPException) as error:
        await ctx.service.enqueue(
            ctx.principal,
            other_conversation["id"],
            "overloaded",
            uuid4().hex,
            uuid4().hex,
        )
    assert error.value.status_code == 429
    async with ctx.factory() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(Message)
                .where(Message.conversation_id == other_conversation["id"])
            )
            == 0
        )


@pytest.mark.asyncio
async def test_feedback_is_owned_and_unresolved_persists_trace_and_citations(ctx):
    run = await ctx.service.enqueue(
        ctx.principal, ctx.conversation, "help", uuid4().hex, uuid4().hex
    )
    claimed = await ctx.service.claim("worker")
    await ctx.service.finalize(
        run["id"],
        claimed["lease_token"],
        "completed",
        {
            "answer": "answer",
            "final_state": "answered",
            "citations": [{"document_id": "doc1"}],
        },
        usage={"input_tokens": 0, "output_tokens": 0},
    )
    result = await ctx.service.get_run(ctx.principal, run["id"])
    message_id = result["result"]["message_id"]
    assert hasattr(ctx.service, "record_feedback"), (
        "production feedback persistence is missing"
    )
    with pytest.raises(HTTPException) as error:
        await ctx.service.record_feedback(ctx.other, message_id, "unresolved")
    assert error.value.status_code == 404
    await ctx.service.record_feedback(ctx.principal, message_id, "unresolved")
    async with ctx.factory() as session:
        message = await session.get(Message, message_id)
        assert message.user_feedback["feedback"] == "unresolved"
        assert message.user_feedback["trace_id"] == run["trace_id"]
        assert message.user_feedback["citation_ids"] == ["doc1"]


@pytest.mark.asyncio
async def test_message_pagination_opens_latest_page_and_loads_earlier_history(ctx):
    from datetime import datetime, timedelta, timezone

    stamp = datetime.now(timezone.utc)
    async with ctx.factory.begin() as session:
        session.add_all(
            [
                Message(
                    conversation_id=ctx.conversation,
                    role="user",
                    content=f"message-{index}",
                    citations=[],
                    created_at=stamp + timedelta(seconds=index),
                )
                for index in range(3)
            ]
        )
    recent = await ctx.service.messages(ctx.principal, ctx.conversation, limit=2)
    assert [item["content"] for item in recent["messages"]] == [
        "message-1",
        "message-2",
    ]
    earlier = await ctx.service.messages(
        ctx.principal, ctx.conversation, cursor=recent["next_cursor"], limit=2
    )
    assert [item["content"] for item in earlier["messages"]] == ["message-0"]
    assert earlier["next_cursor"] is None


@pytest.mark.asyncio
async def test_sse_reconnect_replays_only_later_committed_events_from_another_service_instance(
    ctx,
):
    from tests.production.test_answer_replay import knowledge, provenance

    _, citations = await knowledge(ctx)
    import httpx
    from fastapi import FastAPI
    from app.production.identity import Principal, require_principal
    from app.production.runs import router

    run = await ctx.service.enqueue(
        ctx.principal, ctx.conversation, "help", uuid4().hex, uuid4().hex
    )
    claimed = await ctx.service.claim("worker")
    await ctx.service.append_event(
        run["id"], claimed["lease_token"], "node_completed", {"node": "answer"}
    )
    await ctx.service.finalize(
        run["id"],
        claimed["lease_token"],
        "completed",
        {
            "answer": "persisted",
            "final_state": "answered",
            "citations": citations,
            "knowledge_context": provenance(citations),
        },
        usage={"input_tokens": 0, "output_tokens": 0},
    )
    application = FastAPI()
    application.state.run_service = run_module().RunService(
        ctx.service.settings, ctx.factory, ctx.service.redis
    )
    principal = Principal(
        ctx.principal.user_id, "Owner", "employee", "test-session", "csrf"
    )

    async def authenticated():
        return principal

    async def revalidate(session_id):
        assert session_id == "test-session"
        return principal

    application.dependency_overrides[require_principal] = authenticated
    application.state.identity_service = SimpleNamespace(principal=revalidate)
    application.include_router(router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        response = await client.get(
            f"/api/v1/runs/{run['id']}/events", headers={"Last-Event-ID": "1"}
        )
        assert response.status_code == 200
        assert "id: 1\n" not in response.text
        assert "id: 2\n" in response.text and "id: 3\n" in response.text
        assert '"answer":"persisted"' in response.text
        assert (await client.get(f"/api/v1/runs/{run['id']}/events?after=3")).text == ""


@pytest.mark.asyncio
async def test_user_rate_limit_is_atomic_across_conversations_and_idempotent_replay_is_free(
    ctx,
):
    ctx.service.settings = run_settings(user_rate_limit=1)
    key, client_id = uuid4().hex, uuid4().hex
    first = await ctx.service.enqueue(
        ctx.principal, ctx.conversation, "first", client_id, key
    )
    assert (
        await ctx.service.enqueue(
            ctx.principal, ctx.conversation, "first", client_id, key
        )
    )["id"] == first["id"]
    conversation = await ctx.service.create_conversation(ctx.principal, "second")
    with pytest.raises(HTTPException) as error:
        await ctx.service.enqueue(
            ctx.principal, conversation["id"], "second", uuid4().hex, uuid4().hex
        )
    assert error.value.status_code == 429


@pytest.mark.asyncio
async def test_global_worker_concurrency_is_enforced_across_instances(ctx):
    ctx.service.settings = run_settings(worker_concurrency=2)
    for index in range(4):
        conversation = await ctx.service.create_conversation(ctx.principal, str(index))
        await ctx.service.enqueue(
            ctx.principal, conversation["id"], "help", uuid4().hex, uuid4().hex
        )
    other = run_module().RunService(
        ctx.service.settings, ctx.factory, ctx.service.redis
    )
    claims = await asyncio.gather(
        ctx.service.claim("worker1"),
        other.claim("worker2"),
        ctx.service.claim("worker3"),
        other.claim("worker4"),
    )
    assert sum(claim is not None for claim in claims) == 2


@pytest.mark.asyncio
async def test_real_worker_graph_creates_run_bound_draft_without_legacy_run_or_duplicate_messages(
    ctx,
):
    from app.agent.graph import GraphDependencies
    from app.db.models import AgentRun
    from app.llm.providers import MockChatProvider
    from app.production.business import ProductionTicketService
    from app.production.business_models import TicketDraftRecord
    from app.production.worker import Worker
    from app.services.chat import ChatService

    settings = ctx.service.settings
    settings.session_secret, settings.ticket_confirmation_ttl_seconds = (
        "test-secret-" * 5,
        600,
    )
    tickets = ProductionTicketService(ctx.factory, settings)

    class NoNetworkRetriever:
        async def retrieve(self, *args, **kwargs):
            return []

    async def factory(settings, user_access_level, index_revision):
        return ChatService(
            ctx.factory,
            GraphDependencies(
                NoNetworkRetriever(),
                MockChatProvider(),
                tickets,
                user_access_level=user_access_level,
                require_structured_citations=True,
            ),
            tickets,
        )

    run = await ctx.service.enqueue(
        ctx.principal,
        ctx.conversation,
        "请创建工单\n问题：VPN无法连接\n影响范围：仅本人\n已尝试：尚未尝试",
        uuid4().hex,
        uuid4().hex,
    )
    job = await ctx.service.claim("worker")
    await Worker(settings, ctx.service, tickets, None, chat_factory=factory).execute(
        job
    )
    result = await ctx.service.get_run(ctx.principal, run["id"])
    assert result["status"] == "completed"
    assert result["result"]["final_state"] == "awaiting_confirmation"
    draft = result["result"]["ticket_draft"]
    assert draft["draft_id"] and draft["version"] == 1
    async with ctx.factory() as session:
        record = await session.get(TicketDraftRecord, draft["draft_id"])
        assert record.run_id == run["id"] and record.trace_id == run["trace_id"]
        assert await session.scalar(select(func.count()).select_from(AgentRun)) == 0
        assert (
            await session.scalar(
                select(func.count())
                .select_from(Message)
                .where(Message.conversation_id == ctx.conversation)
            )
            == 2
        )


@pytest.mark.asyncio
async def test_uncertain_model_usage_consumes_full_reservation_and_cannot_settle_twice(
    ctx,
):
    from decimal import Decimal
    from datetime import datetime, timezone

    run = await ctx.service.enqueue(
        ctx.principal, ctx.conversation, "help", uuid4().hex, uuid4().hex
    )
    claimed = await ctx.service.claim("worker")
    usage = {"input_tokens": 1, "output_tokens": 1, "usage_uncertain": True}
    assert await ctx.service.finalize(
        run["id"],
        claimed["lease_token"],
        "completed",
        {"answer": "answer", "final_state": "answered"},
        usage=usage,
    )
    assert not await ctx.service.finalize(
        run["id"],
        claimed["lease_token"],
        "completed",
        {"answer": "duplicate"},
        usage=usage,
    )
    async with ctx.factory() as session:
        budget = await session.get(
            ctx.models.ModelBudget, datetime.now(timezone.utc).strftime("%Y-%m")
        )
        assert budget.reserved == 0 and budget.spent == Decimal("0.019")


@pytest.mark.asyncio
async def test_investigation_state_retains_evidence_hypotheses_and_completed_subtasks(
    ctx,
):
    run = await ctx.service.enqueue(
        ctx.principal,
        ctx.conversation,
        "investigate",
        uuid4().hex,
        uuid4().hex,
        task_mode="investigate",
    )
    claimed = await ctx.service.claim("worker")
    report = {
        "observations": [
            {"id": "public-observation", "status": "ok", "origin": "snapshot"}
        ],
        "hypotheses": [
            {"text": "版本变化可能有关", "verification": "requires_semantic_review"}
        ],
        "subtasks": [
            {"agent": "logs_metrics", "error": None},
            {"agent": "knowledge_changes", "error": "timeout"},
        ],
        "unknowns": ["deployment history missing"],
    }
    assert await ctx.service.finalize(
        run["id"],
        claimed["lease_token"],
        "completed",
        {
            "answer": "只读报告",
            "final_state": "investigated",
            "investigation_report": report,
        },
    )
    async with ctx.factory() as session:
        record = await session.get(ctx.models.ProductionRun, run["id"])
        assert record.agent_state["evidence_ids"] == ["public-observation"]
        assert record.agent_state["hypotheses"] == report["hypotheses"]
        assert record.agent_state["completed_subtasks"] == ["logs_metrics"]
        assert record.agent_state["unknowns"] == report["unknowns"]


@pytest.mark.asyncio
async def test_current_service_scope_filters_run_events_and_message_history(ctx):
    run = await ctx.service.enqueue(
        ctx.principal,
        ctx.conversation,
        "investigate",
        uuid4().hex,
        uuid4().hex,
        task_mode="investigate",
    )
    claimed = await ctx.service.claim("worker")
    result = {
        "answer": "private-checkout-log",
        "final_state": "investigated",
        "investigation_report": {
            "observations": [
                {
                    "id": "log-1",
                    "tool": "query_error_logs",
                    "status": "ok",
                    "resource": "checkout",
                    "data": {"items": ["private-checkout-log"]},
                }
            ]
        },
    }
    assert await ctx.service.finalize(
        run["id"], claimed["lease_token"], "completed", result
    )
    ctx.service.settings.investigation_allowed_services = ()
    current = await ctx.service.get_run(ctx.principal, run["id"])
    assert current["result"]["access_redacted"]
    assert "private-checkout-log" not in str(current["events"])
    history = await ctx.service.messages(ctx.principal, ctx.conversation)
    assert "private-checkout-log" not in str(history)


@pytest.mark.asyncio
async def test_timed_out_investigation_settles_reserved_monthly_cost(ctx):
    from datetime import datetime, timezone
    from app.agent.graph import GraphDependencies
    from app.agent.investigation import Investigator
    from app.agent.operations_tools import (
        OperationsScope,
        OperationsTools,
        SnapshotReader,
    )
    from app.production.worker import Worker
    from app.services.chat import ChatService

    class Provider:
        async def complete(self, prompt):
            raise TimeoutError("public synthetic timeout")

    async def factory(settings, **kwargs):
        provider = Provider()
        return ChatService(
            ctx.factory,
            GraphDependencies(
                object(),
                provider,
                object(),
                investigator=Investigator(
                    provider,
                    OperationsTools(reader=SnapshotReader({}, "public-fixture")),
                ),
                investigation_scope=OperationsScope(
                    ctx.principal.user_id, "employee", frozenset({"checkout"})
                ),
            ),
            object(),
        )

    run = await ctx.service.enqueue(
        ctx.principal,
        ctx.conversation,
        "调查checkout",
        uuid4().hex,
        uuid4().hex,
        task_mode="investigate",
    )
    async with ctx.factory() as session:
        reserved = (await session.get(ctx.models.ProductionRun, run["id"])).reserved_cost
        assert reserved > 0
    claimed = await ctx.service.claim("worker")
    await Worker(
        ctx.service.settings, ctx.service, object(), None, chat_factory=factory
    ).execute(claimed)
    current = await ctx.service.get_run(ctx.principal, run["id"])
    assert (
        current["status"] == "completed"
        and current["result"]["investigation_report"]["status"] == "partial"
    )
    async with ctx.factory() as session:
        budget = await session.get(
            ctx.models.ModelBudget, datetime.now(timezone.utc).strftime("%Y-%m")
        )
        assert budget.reserved == 0 and budget.spent == reserved
