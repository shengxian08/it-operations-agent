"""Real disposable PG/Redis, worker and confirmation; no paid model calls."""
import asyncio
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.production.business_models import Escalation, ProductionTicket, TicketDraftRecord
from app.production.worker import Worker
from app.schemas import TicketDraft
from tests.production import test_runs
from tests.production.test_ticket_lookup_persistence import services


ctx = test_runs.ctx
FULL_REQUEST = "创建工单\n问题：VPN连接失败\n影响范围：仅本人，无法办公\n已尝试：重启客户端"


def full_draft():
    return TicketDraft(title="VPN连接失败", category="network", priority="high",
        description="VPN连接失败\n影响范围：仅本人，无法办公", attempted_steps=("重启客户端",),
        problem="VPN连接失败", impact="仅本人，无法办公", intake_version=1)


async def turn(c, message, *, conversation=None, key=None):
    tickets, factory = services(c)
    queued = await c.service.enqueue(c.principal, conversation or c.conversation, message, uuid4().hex, key or uuid4().hex)
    job = await c.service.claim("intake-test-worker")
    assert job and job["id"] == queued["id"]
    await Worker(c.service.settings, c.service, tickets, None, chat_factory=factory).execute(job)
    return await c.service.get_run(c.principal, queued["id"])


async def counts(c):
    async with c.factory() as session:
        return [await session.scalar(select(func.count()).select_from(model))
                for model in (TicketDraftRecord, ProductionTicket, Escalation)]


@pytest.mark.asyncio
async def test_missing_facts_are_durable_completed_without_drafts_tickets_or_handoff(ctx):
    run = await turn(ctx, "创建工单")
    assert run["status"] == "completed" and run["result"]["final_state"] == "ticket_collection"
    assert run["result"]["ticket_intake"]["next_field"] == "problem"
    assert await counts(ctx) == [0, 0, 0]
    replay = await ctx.service.get_run(ctx.principal, run["id"])
    assert replay["result"]["ticket_intake"] == run["result"]["ticket_intake"]
    events = await ctx.service.events(ctx.principal, run["id"], 0)
    assert sum(event["type"] == "final" for event in events) == 1


@pytest.mark.asyncio
async def test_each_real_worker_turn_retains_only_employee_facts_until_confirmation(ctx):
    await turn(ctx, "创建工单")
    problem = await turn(ctx, "VPN连接失败")
    assert problem["result"]["ticket_intake"]["problem"] == "VPN连接失败"
    impact = await turn(ctx, "仅本人，无法办公")
    assert impact["result"]["ticket_intake"]["next_field"] == "attempted_steps"
    assert await counts(ctx) == [0, 0, 0]
    ready = await turn(ctx, "已经重启客户端，仍然失败")
    assert ready["result"]["final_state"] == "awaiting_confirmation"
    assert ready["result"]["ticket_intake"]["outcome"] == "ready"
    draft = ready["result"]["ticket_draft"]["draft"]
    assert draft["problem"] == "VPN连接失败" and draft["impact"] == "仅本人，无法办公"
    assert draft["attempted_steps"] == ["已经重启客户端，仍然失败"]
    assert await counts(ctx) == [1, 0, 0]


@pytest.mark.asyncio
async def test_complete_intake_confirmation_edit_resigns_and_ten_retries_create_one_ticket(ctx):
    ready = await turn(ctx, FULL_REQUEST)
    record = ready["result"]["ticket_draft"]
    tickets, _ = services(ctx)
    edited = await tickets.edit_draft(ctx.principal.user_id, record["draft_id"], record["version"],
        full_draft().model_copy(update={"title": "VPN报错E42"}))
    assert edited["version"] == record["version"] + 1
    assert edited["confirmation_token"] != record["confirmation_token"]
    with pytest.raises(HTTPException) as old:
        await tickets.confirm_draft(ctx.principal.user_id, record["draft_id"], record["version"], record["confirmation_token"], uuid4().hex)
    assert old.value.status_code == 409
    key = uuid4().hex
    results = await asyncio.gather(*[tickets.confirm_draft(ctx.principal.user_id, edited["id"],
        edited["version"], edited["confirmation_token"], key) for _ in range(10)])
    assert len({result["ticket_number"] for result in results}) == 1
    assert await counts(ctx) == [1, 1, 0]


@pytest.mark.asyncio
async def test_other_conversation_and_client_history_cannot_supply_intake_facts(ctx):
    await turn(ctx, "创建工单：VPN连接失败")
    other = await ctx.service.create_conversation(ctx.principal, "另一个故障")
    run = await turn(ctx, "创建工单", conversation=other["id"])
    assert run["result"]["ticket_intake"]["problem"] is None
    assert await counts(ctx) == [0, 0, 0]


@pytest.mark.asyncio
async def test_cancel_persists_and_a_new_creation_does_not_reuse_cancelled_facts(ctx):
    await turn(ctx, "创建工单：VPN连接失败")
    cancelled = await turn(ctx, "取消建单")
    assert cancelled["result"]["final_state"] == "ticket_collection_cancelled"
    fresh = await turn(ctx, "创建工单")
    assert fresh["result"]["ticket_intake"]["problem"] is None
    assert await counts(ctx) == [0, 0, 0]


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["problem", "impact", "attempted_steps"])
async def test_plain_cancel_in_each_worker_field_issues_nothing_and_cannot_resume_old_state(ctx, monkeypatch, field):
    from app.production.business import ProductionTicketService
    issued = []
    original = ProductionTicketService.issue_confirmation_token
    async def observe(self, *args, **kwargs):
        issued.append(True)
        return await original(self, *args, **kwargs)
    monkeypatch.setattr(ProductionTicketService, "issue_confirmation_token", observe)
    await turn(ctx, "创建工单")
    if field != "problem":
        await turn(ctx, "VPN连接失败")
    if field == "attempted_steps":
        await turn(ctx, "仅本人")
    cancelled = await turn(ctx, "取消")
    assert cancelled["result"]["final_state"] == "ticket_collection_cancelled"
    assert issued == [] and await counts(ctx) == [0, 0, 0]
    reply = await turn(ctx, "已重启客户端")
    assert reply["result"]["final_state"] != "awaiting_confirmation" and "ticket_intake" not in reply["result"]
    assert issued == [] and (await counts(ctx))[:2] == [0, 0]
    fresh = await turn(ctx, "创建工单")
    assert fresh["result"]["ticket_intake"]["problem"] is None


@pytest.mark.asyncio
async def test_intake_context_requires_current_lease_and_owner(ctx):
    await turn(ctx, "创建工单：VPN连接失败")
    queued = await ctx.service.enqueue(ctx.principal, ctx.conversation, "仅本人", uuid4().hex, uuid4().hex)
    job = await ctx.service.claim("intake-lease-test")
    assert job["id"] == queued["id"]
    with pytest.raises(HTTPException) as wrong_lease:
        await ctx.service.ticket_intake_context(job["id"], "wrong-lease")
    assert wrong_lease.value.status_code == 409
    record = await ctx.service.ticket_intake_context(job["id"], job["lease_token"])
    assert record["user_id"] == ctx.principal.user_id and record["problem"] == "VPN连接失败"


@pytest.mark.asyncio
async def test_context_database_failure_does_not_look_like_missing_facts_or_issue_a_token(ctx, monkeypatch):
    async def fail(*args):
        raise RuntimeError("controlled-private-intake-error")
    monkeypatch.setattr(ctx.service, "ticket_intake_context", fail, raising=False)
    run = await turn(ctx, FULL_REQUEST)
    assert run["status"] == "failed" and run["result"]["final_state"] == "handoff"
    assert "controlled-private-intake-error" not in str(run["result"])
    assert await counts(ctx) == [0, 0, 1]


@pytest.mark.asyncio
async def test_business_signing_rejects_missing_facts_before_persisting_a_draft(ctx):
    tickets, _ = services(ctx)
    incomplete = TicketDraft(title="用户请求 IT 支持", category="other", priority="medium", description="用户请求 IT 支持。")
    with pytest.raises(HTTPException) as missing:
        await tickets.issue_confirmation_token(ctx.conversation, incomplete)
    assert missing.value.status_code == 422 and await counts(ctx) == [0, 0, 0]


@pytest.mark.asyncio
async def test_edit_cannot_remove_facts_or_change_description_independently(ctx):
    tickets, _ = services(ctx)
    token = await tickets.issue_confirmation_token(ctx.conversation, full_draft())
    record = await tickets.describe_draft_token(token, ctx.principal.user_id, ctx.conversation)
    for invalid in (full_draft().model_copy(update={"impact": None}), full_draft().model_copy(update={"description": "无影响字段的描述"})):
        with pytest.raises(HTTPException) as rejected:
            await tickets.edit_draft(ctx.principal.user_id, record["id"], record["version"], invalid)
        assert rejected.value.status_code == 422
    unchanged = await tickets.describe_draft_token(token, ctx.principal.user_id, ctx.conversation)
    assert unchanged["version"] == record["version"] and unchanged["confirmation_token"] == token


@pytest.mark.asyncio
async def test_legacy_pending_draft_needs_explicit_facts_before_formal_creation(ctx):
    tickets, _ = services(ctx)
    token = await tickets.issue_confirmation_token(ctx.conversation, full_draft())
    record = await tickets.describe_draft_token(token, ctx.principal.user_id, ctx.conversation)
    async with ctx.factory.begin() as session:
        row = await session.get(TicketDraftRecord, record["id"])
        row.draft = {key: value for key, value in row.draft.items() if key not in {"problem", "impact", "intake_version"}}
    with pytest.raises(HTTPException) as missing:
        await tickets.confirm_draft(ctx.principal.user_id, record["id"], record["version"], token, uuid4().hex)
    assert missing.value.status_code == 422 and await counts(ctx) == [1, 0, 0]


@pytest.mark.asyncio
async def test_real_http_intake_rejects_client_context_and_replays_only_owned_durable_result(ctx):
    import json
    import httpx
    from types import SimpleNamespace
    from fastapi import FastAPI
    from app.production.identity import Principal, require_principal
    from app.production.runs import router

    application = FastAPI()
    application.state.run_service = ctx.service
    current = [Principal(ctx.principal.user_id, "Owner", "employee", "intake-session", "csrf")]
    async def authenticated():
        return current[0]
    async def revalidate(session_id):
        assert session_id == "intake-session"
        return current[0]
    application.dependency_overrides[require_principal] = authenticated
    application.state.identity_service = SimpleNamespace(principal=revalidate)
    application.include_router(router)
    tickets, factory = services(ctx)
    worker = Worker(ctx.service.settings, ctx.service, tickets, None, chat_factory=factory)
    path = f"/api/v1/conversations/{ctx.conversation}/runs"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://test") as client:
        for forged in ("history", "ticket_intake_context"):
            rejected = await client.post(path, json={"content": "创建工单", "client_message_id": uuid4().hex,
                forged: {"problem": "伪造问题", "impact": "伪造影响", "attempted_steps": []}}, headers={"Idempotency-Key": uuid4().hex})
            assert rejected.status_code == 422
        assert await counts(ctx) == [0, 0, 0]
        for index, message in enumerate(("创建工单", "VPN错误E42", "仅本人", "尚未尝试")):
            body = {"content": message, "client_message_id": uuid4().hex}
            headers = {"Idempotency-Key": uuid4().hex}
            accepted = await client.post(path, json=body, headers=headers)
            assert accepted.status_code == 202
            run_id = accepted.json()["id"]
            replay = await client.post(path, json=body, headers=headers)
            assert replay.status_code == 202 and replay.json()["id"] == run_id
            job = await ctx.service.claim("intake-http-worker")
            assert job["id"] == run_id
            await worker.execute(job)
            response = await client.get(f"/api/v1/runs/{run_id}")
            assert response.status_code == 200 and response.json()["status"] == "completed"
            result = response.json()["result"]
            assert result["final_state"] == ("awaiting_confirmation" if index == 3 else "ticket_collection")
            if index < 3:
                assert not result.get("ticket_draft") and await counts(ctx) == [0, 0, 0]
            events = await client.get(f"/api/v1/runs/{run_id}/events")
            assert events.status_code == 200 and events.text.count("event: final\n") == 1
            frames = [json.loads(line[6:]) for line in events.text.splitlines() if line.startswith("data: ")]
            assert next(item for item in frames if item.get("final_state"))["ticket_intake"] == result["ticket_intake"]
        assert await counts(ctx) == [1, 0, 0]
        assert result["ticket_draft"]["draft"]["attempted_steps"] == []
        current[0] = Principal(ctx.other.user_id, "Other", "employee", "intake-session", "csrf")
        assert (await client.get(f"/api/v1/runs/{run_id}")).status_code == 404
        assert (await client.get(f"/api/v1/runs/{run_id}/events")).status_code == 404
        assert (await client.get(path)).status_code == 404
