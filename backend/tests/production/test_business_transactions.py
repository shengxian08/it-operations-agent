"""Run against the isolated integration database, never an implicit local database."""
import asyncio
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import Base, Conversation, Message, User
from app.production.business import ProductionTicketService, TicketPatch
from app.production.business_models import BusinessAudit, Escalation, ProductionTicket, TicketConfirmation, TicketCounter, TicketDraftRecord, ProductionTicketComment
from app.production.identity import Principal
from app.production.identity_models import IdentityAccount
from app.production.run_models import ProductionRun
from app.schemas import TicketDraft


@pytest_asyncio.fixture
async def business_context():
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("explicit isolated TEST_DATABASE_URL is required")
    schema = "business_test_" + uuid4().hex
    bootstrap = create_async_engine(url)
    async with bootstrap.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    tables = [User.__table__, IdentityAccount.__table__, Conversation.__table__, Message.__table__, ProductionRun.__table__, TicketDraftRecord.__table__, TicketCounter.__table__, ProductionTicket.__table__, TicketConfirmation.__table__, ProductionTicketComment.__table__, BusinessAudit.__table__, Escalation.__table__]
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: Base.metadata.create_all(sync, tables=tables))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    owner, other, support = ["business-test-" + uuid4().hex for _ in range(3)]
    conversation = "business-test-" + uuid4().hex
    async with factory.begin() as session:
        session.add_all([User(id=owner, display_name="Owner", access_level="employee"), User(id=other, display_name="Other", access_level="employee"), User(id=support, display_name="Support", access_level="support")])
        await session.flush()
        session.add_all([IdentityAccount(user_id=user_id, issuer="https://test-identity", subject=user_id)
                         for user_id in (owner, other, support)])
        session.add(Conversation(id=conversation, user_id=owner))
    service = ProductionTicketService(factory, SimpleNamespace(session_secret="integration-secret", ticket_confirmation_ttl_seconds=600))
    context = SimpleNamespace(service=service, factory=factory, owner=owner, other=other, support=support, conversation=conversation)
    try:
        yield context
    finally:
        await engine.dispose()
        async with bootstrap.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await bootstrap.dispose()


def sample_draft(title="VPN错误"):
    return TicketDraft(title=title, category="vpn", priority="medium", description="VPN无法连接\n影响范围：仅本人", attempted_steps=("重启",), problem="VPN无法连接", impact="仅本人", intake_version=1)


@pytest.mark.asyncio
async def test_simultaneous_confirmation_replays_without_trace_dependency(business_context):
    c = business_context
    token = await c.service.issue_confirmation_token(c.conversation, sample_draft(), trace_id="original")
    row = await c.service.describe_draft_token(token, c.owner, c.conversation)
    results = await asyncio.gather(*[c.service.confirm_draft(c.owner, row["id"], row["version"], token, "concurrent") for _ in range(10)])
    assert all(result == results[0] for result in results)
    async with c.factory() as session:
        assert await session.scalar(select(func.count()).select_from(ProductionTicket).where(ProductionTicket.user_id == c.owner)) == 1
        stored = await session.get(TicketDraftRecord, row["id"])
        assert stored.token_hash != token
        assert stored.status == "confirmed"
    assert await c.service.confirm_draft(c.owner, row["id"], row["version"], token, "concurrent") == results[0]


@pytest.mark.asyncio
async def test_edit_rotates_token_and_owner_is_enforced(business_context):
    c = business_context
    token = await c.service.issue_confirmation_token(c.conversation, sample_draft())
    row = await c.service.describe_draft_token(token, c.owner, c.conversation)
    with pytest.raises(HTTPException) as hidden:
        await c.service.confirm_draft(c.other, row["id"], 1, token, "other")
    assert hidden.value.status_code == 404
    changed = await c.service.edit_draft(c.owner, row["id"], 1, sample_draft("新标题"))
    assert changed["version"] == 2
    assert changed["confirmation_token"] != token
    with pytest.raises(HTTPException) as stale:
        await c.service.confirm_draft(c.owner, row["id"], 1, token, "stale")
    assert stale.value.status_code == 409
    result = await c.service.confirm_draft(c.owner, row["id"], 2, changed["confirmation_token"], "new")
    support = Principal(c.support, "Support", "support")
    ticket = await c.service.update_ticket(support, result["ticket_number"], TicketPatch(version=1, status="in_progress", assignee_id=c.support))
    assert ticket["version"] == 2 and ticket["assignee_id"] == c.support
    with pytest.raises(HTTPException) as conflict:
        await c.service.update_ticket(support, result["ticket_number"], TicketPatch(version=1, status="resolved"))
    assert conflict.value.status_code == 409
    await c.service.update_ticket(support, result["ticket_number"], TicketPatch(version=2, status="resolved"))
    owner = Principal(c.owner, "Owner", "employee")
    reopened = await c.service.update_ticket(owner, result["ticket_number"], TicketPatch(version=3, status="in_progress"))
    assert reopened["status"] == "in_progress"


@pytest.mark.asyncio
async def test_handoff_persists_an_escalation_without_ticket(business_context):
    c = business_context
    run_id = await make_running_run(c)
    first = await c.service.record_handoff(c.owner, c.conversation, run_id, "缺少权限", "trace-a")
    second = await c.service.record_handoff(c.owner, c.conversation, run_id, "缺少权限", "trace-b")
    assert first["id"] == second["id"]
    async with c.factory() as session:
        assert await session.scalar(select(func.count()).select_from(ProductionTicket).where(ProductionTicket.user_id == c.owner)) == 0


async def make_running_run(c):
    from datetime import datetime, timedelta, timezone
    from decimal import Decimal
    run_id, message_id = str(uuid4()), str(uuid4())
    async with c.factory.begin() as session:
        session.add(Message(id=message_id, conversation_id=c.conversation, role="user", content="测试"))
        await session.flush()
        session.add(ProductionRun(id=run_id, user_id=c.owner, conversation_id=c.conversation, message_id=message_id,
            client_message_id=run_id, idempotency_key=run_id, content_hash="x" * 64, status="running", trace_id="test-trace",
            user_access_level="employee", index_revision="", lease_token="current-lease",
            lease_expires_at=datetime.now(timezone.utc) + timedelta(seconds=30),
            deadline_at=datetime.now(timezone.utc) + timedelta(seconds=120), budget_month="2026-10", reserved_cost=Decimal(0),
            input_price=Decimal(0), output_price=Decimal(0)))
    return run_id


@pytest.mark.asyncio
async def test_business_side_effects_are_fenced_by_cancelled_run_or_old_lease(business_context):
    c = business_context
    run_id = await make_running_run(c)
    with pytest.raises(PermissionError):
        await c.service.record_handoff(c.owner, c.conversation, run_id, "reason", "trace", lease_token="old-lease")
    with pytest.raises(PermissionError):
        await c.service.issue_confirmation_token(c.conversation, sample_draft(), run_id=run_id, lease_token="old-lease")
    async with c.factory.begin() as session:
        run = await session.get(ProductionRun, run_id)
        run.status = "cancelled"
    with pytest.raises(PermissionError):
        await c.service.record_handoff(c.owner, c.conversation, run_id, "reason", "trace", lease_token="current-lease")
    with pytest.raises(PermissionError):
        await c.service.issue_confirmation_token(c.conversation, sample_draft(), run_id=run_id, lease_token="current-lease")
    async with c.factory() as session:
        assert await session.scalar(select(func.count()).select_from(TicketDraftRecord).where(TicketDraftRecord.user_id == c.owner)) == 0
        assert await session.scalar(select(func.count()).select_from(Escalation).where(Escalation.user_id == c.owner)) == 0


@pytest.mark.asyncio
async def test_confirmation_rollback_keeps_token_reusable(business_context):
    from sqlalchemy import event
    from sqlalchemy.orm import Session
    c = business_context
    token = await c.service.issue_confirmation_token(c.conversation, sample_draft())
    row = await c.service.describe_draft_token(token, c.owner, c.conversation)
    def fail_confirmation_flush(session, *_):
        if any(isinstance(item, TicketConfirmation) and item.user_id == c.owner for item in session.new):
            raise RuntimeError("injected transaction failure")
    event.listen(Session, "before_flush", fail_confirmation_flush)
    try:
        with pytest.raises(RuntimeError, match="injected"):
            await c.service.confirm_draft(c.owner, row["id"], 1, token, "retry-after-rollback")
    finally:
        event.remove(Session, "before_flush", fail_confirmation_flush)
    async with c.factory() as session:
        draft = await session.get(TicketDraftRecord, row["id"])
        assert draft.status == "pending"
        assert await session.scalar(select(func.count()).select_from(ProductionTicket).where(ProductionTicket.user_id == c.owner)) == 0
    assert (await c.service.confirm_draft(c.owner, row["id"], 1, token, "retry-after-rollback"))["ticket_number"]


@pytest.mark.asyncio
async def test_expiry_and_same_key_different_payload_are_rejected(business_context):
    from datetime import datetime, timedelta, timezone
    c = business_context
    token = await c.service.issue_confirmation_token(c.conversation, sample_draft())
    row = await c.service.describe_draft_token(token, c.owner, c.conversation)
    await c.service.confirm_draft(c.owner, row["id"], 1, token, "used")
    another = await c.service.issue_confirmation_token(c.conversation, sample_draft("第二张"))
    next_row = await c.service.describe_draft_token(another, c.owner, c.conversation)
    with pytest.raises(HTTPException) as reused:
        await c.service.confirm_draft(c.owner, next_row["id"], 1, another, "used")
    assert reused.value.status_code == 409
    async with c.factory.begin() as session:
        expired = await session.get(TicketDraftRecord, next_row["id"])
        expired.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    with pytest.raises(HTTPException) as expired:
        await c.service.confirm_draft(c.owner, next_row["id"], 1, another, "expired")
    assert expired.value.status_code == 410


@pytest.mark.asyncio
async def test_counter_allocates_the_10000th_ticket(business_context, monkeypatch):
    from datetime import datetime, timezone
    from sqlalchemy.dialects.postgresql import insert
    c = business_context
    monkeypatch.setattr("app.production.business.now", lambda: datetime(1908, 1, 1, tzinfo=timezone.utc))
    async with c.factory.begin() as session:
        await session.execute(insert(TicketCounter).values(year=1908, value=9999).on_conflict_do_update(index_elements=[TicketCounter.year], set_={"value": 9999}))
    token = await c.service.issue_confirmation_token(c.conversation, sample_draft())
    row = await c.service.describe_draft_token(token, c.owner, c.conversation)
    result = await c.service.confirm_draft(c.owner, row["id"], 1, token, "large-sequence")
    assert result["ticket_number"] == "IT-1908-10000"
    async with c.factory.begin() as session:
        await session.execute(delete(TicketCounter).where(TicketCounter.year == 1908))


@pytest.mark.asyncio
async def test_expired_draft_can_be_edited_to_issue_fresh_token_and_confirmed_result_is_recoverable(business_context):
    from datetime import datetime, timedelta, timezone
    c = business_context
    token = await c.service.issue_confirmation_token(c.conversation, sample_draft())
    row = await c.service.describe_draft_token(token, c.owner, c.conversation)
    async with c.factory.begin() as session:
        draft = await session.get(TicketDraftRecord, row["id"])
        draft.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    renewed = await c.service.edit_draft(c.owner, row["id"], 1, sample_draft())
    assert renewed["version"] == 2 and renewed["confirmation_token"] != token
    result = await c.service.confirm_draft(c.owner, row["id"], 2, renewed["confirmation_token"], "renewed")
    drafts = await c.service.list_drafts(c.owner, c.conversation)
    assert drafts[0]["ticket_number"] == result["ticket_number"]
    assert drafts[0]["status"] == "confirmed" and drafts[0]["confirmation_token"] is None


@pytest.mark.asyncio
async def test_chat_ticket_status_uses_current_support_scope(business_context):
    c = business_context
    token = await c.service.issue_confirmation_token(c.conversation, sample_draft())
    row = await c.service.describe_draft_token(token, c.owner, c.conversation)
    created = await c.service.confirm_draft(c.owner, row["id"], 1, token, "support-status")
    assert (await c.service.get_ticket_status(c.owner, created["ticket_number"])).found
    assert not (await c.service.get_ticket_status(c.other, created["ticket_number"])).found
    assert (await c.service.get_ticket_status(c.support, created["ticket_number"])).found


@pytest.mark.asyncio
async def test_business_api_checks_session_csrf_roles_and_owner(business_context):
    import json
    import httpx
    from fastapi import FastAPI
    from redis.asyncio import Redis
    from app.production.business import router
    from app.production.knowledge import router as knowledge_router
    from app.production.identity import IdentityService
    from app.production.identity_models import IdentityAccount
    c = business_context
    redis_url = os.getenv("TEST_REDIS_URL")
    if not redis_url:
        pytest.skip("explicit isolated Redis URL is required")
    redis = Redis.from_url(redis_url)
    config = SimpleNamespace(session_secret="integration-secret", oidc_http_timeout_seconds=5,
        session_ttl_seconds=3600, session_cookie_name="it_assistant_session", public_base_url="https://assistant.test",
        oidc_allowed_roles=("employee", "support", "admin"))
    identity = IdentityService(config, redis, c.factory)
    # The shared fixture now supplies the current enabled identity for each user.
    session_ids = {user_id: "business-test-" + uuid4().hex for user_id in [c.owner, c.other, c.support]}
    for user_id, sid in session_ids.items():
        await redis.set(identity._key("session", sid), json.dumps({"user_id": user_id, "csrf_token": "test-csrf"}), ex=3600)
    app = FastAPI()
    app.state.identity_service, app.state.ticket_service = identity, c.service
    app.include_router(router)
    app.include_router(knowledge_router)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="https://assistant.test") as client:
            assert (await client.get("/api/v1/tickets", headers={"Authorization": "Bearer arbitrary", "X-User-ID": c.owner})).status_code == 401
            client.cookies.set(config.session_cookie_name, session_ids[c.owner])
            token = await c.service.issue_confirmation_token(c.conversation, sample_draft())
            row = await c.service.describe_draft_token(token, c.owner, c.conversation)
            endpoint = f"/api/v1/ticket-drafts/{row['id']}/confirm"
            body = {"version": 1, "confirmation_token": token}
            assert (await client.post(endpoint, json=body, headers={"Idempotency-Key": "api"})).status_code == 403
            headers = {"Origin": "https://assistant.test", "X-CSRF-Token": "test-csrf", "Idempotency-Key": "api"}
            created = await client.post(endpoint, json=body, headers=headers)
            assert created.status_code == 200
            number = created.json()["ticket_number"]
            assert (await client.patch(f"/api/v1/tickets/{number}", json={"version": 1, "status": "resolved"}, headers=headers)).status_code == 403
            assert (await client.get("/api/v1/knowledge/jobs")).status_code == 403
            client.cookies.set(config.session_cookie_name, session_ids[c.other])
            assert (await client.get(f"/api/v1/tickets/{number}")).status_code == 404
            client.cookies.set(config.session_cookie_name, session_ids[c.support])
            updated = await client.patch(f"/api/v1/tickets/{number}", json={"version": 1, "status": "in_progress", "assignee_id": c.support}, headers=headers)
            assert updated.status_code == 200 and updated.json()["version"] == 2
            comment = await client.post(f"/api/v1/tickets/{number}/comments", json={"version": 2, "content": "处理中"}, headers=headers)
            assert comment.status_code == 200 and comment.json()["version"] == 3
            detail = await client.get(f"/api/v1/tickets/{number}")
            assert detail.json()["comments"][0]["content"] == "处理中"
            assert len(detail.json()["audit"]) == 3
            async with c.factory.begin() as session:
                account = await session.scalar(select(IdentityAccount).where(IdentityAccount.user_id == c.support))
                account.enabled = False
            assert (await client.get("/api/v1/tickets")).status_code == 401
    finally:
        for sid in session_ids.values():
            await redis.delete(identity._key("session", sid))
        await identity.close()
        await redis.aclose()

