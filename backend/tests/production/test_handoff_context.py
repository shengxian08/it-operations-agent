"""Disposable PG/Redis and actual worker/API; employee facts and bounded references."""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from sqlalchemy import event, func, select, update

from app.db.models import Conversation, Message, User
from app.production.business import router
from app.production.business_models import BusinessAudit, Escalation, ProductionTicket
from app.production.identity import Principal, require_principal
from app.production.identity_models import IdentityAccount
from tests.production import test_runs
from tests.production.test_ticket_intake_persistence import FULL_REQUEST, turn
from tests.production.test_ticket_lookup_persistence import services

ctx = test_runs.ctx


async def handoff(c):
    result = await turn(c, "请转人工")
    assert result["result"]["handoff_reason"] == "explicit_manual_request"
    return result["result"]["escalation_id"]


async def stored_context(c, identifier):
    async with c.factory() as session:
        row = await session.get(Escalation, identifier)
        return getattr(row, "context", None)


async def running(c, message="VPN连接失败，请转人工"):
    queued = await c.service.enqueue(c.principal, c.conversation, message, uuid4().hex, uuid4().hex)
    job = await c.service.claim("handoff-test-worker")
    assert job["id"] == queued["id"]
    return job


def client(c, tickets, actor):
    app = FastAPI()
    app.state.ticket_service = tickets
    async def authenticated():
        return actor[0]
    app.dependency_overrides[require_principal] = authenticated
    app.include_router(router)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def support(c, *, enabled=True):
    identifier = uuid4().hex
    async with c.factory.begin() as session:
        session.add(User(id=identifier, display_name="Support", access_level="support"))
        await session.flush()
        session.add(IdentityAccount(user_id=identifier, issuer="https://test", subject=identifier, enabled=enabled))
    return Principal(identifier, "Support", "support")


@pytest.mark.asyncio
@pytest.mark.parametrize("revocation", ["demote", "disable"])
@pytest.mark.parametrize("action", ["status", "assign"])
async def test_http_escalation_rechecks_actor_after_lock_wait_without_side_effects(ctx, revocation, action):
    tickets, _ = services(ctx)
    identifier = await handoff(ctx)
    staff = await support(ctx)
    assignee = await support(ctx)
    reached = asyncio.Event()
    engine = ctx.factory.kw["bind"].sync_engine

    def waiting(_connection, _cursor, statement, _parameters, _context, _many):
        if f"FROM {Escalation.__tablename__}" in statement and "FOR UPDATE" in statement:
            reached.set()

    body = {"version": 1, **({"status": "in_progress"} if action == "status" else {"assignee_id": assignee.user_id})}
    async with client(ctx, tickets, [staff]) as api:
        async with ctx.factory.begin() as blocker:
            await blocker.scalar(select(Escalation).where(Escalation.id == identifier).with_for_update())
            event.listen(engine, "before_cursor_execute", waiting)
            pending = asyncio.create_task(api.patch(f"/api/v1/escalations/{identifier}", json=body))
            try:
                await asyncio.wait_for(reached.wait(), 5)
                async with ctx.factory.begin() as session:
                    if revocation == "demote":
                        await session.execute(update(User).where(User.id == staff.user_id).values(access_level="employee"))
                    else:
                        await session.execute(update(IdentityAccount).where(IdentityAccount.user_id == staff.user_id).values(enabled=False))
            finally:
                event.remove(engine, "before_cursor_execute", waiting)
        response = await asyncio.wait_for(pending, 5)
    assert response.status_code == 403
    async with ctx.factory() as session:
        row = await session.get(Escalation, identifier)
        assert row.status == "pending" and row.version == 1 and row.assignee_id is None
        assert await session.scalar(select(func.count()).select_from(BusinessAudit).where(BusinessAudit.entity_id == identifier)) == 1


@pytest.mark.asyncio
async def test_explicit_handoff_carries_collected_employee_facts_and_source_draft(ctx):
    ready = await turn(ctx, FULL_REQUEST)
    identifier = await handoff(ctx)
    snapshot = await stored_context(ctx, identifier)
    assert snapshot and snapshot["schema_version"] == 1
    assert snapshot["problem"] == "VPN连接失败" and snapshot["impact"] == "仅本人，无法办公"
    assert snapshot["attempted_steps"] == ["重启客户端"]
    assert snapshot["source"]["draft_id"] == ready["result"]["ticket_draft"]["draft_id"]
    assert snapshot["source"]["run_id"] == ready["id"]
    assert snapshot["messages"][-1]["content"] == "请转人工"
    async with ctx.factory() as session:
        assert await session.scalar(select(func.count()).select_from(ProductionTicket)) == 0


@pytest.mark.asyncio
async def test_snapshot_uses_only_owned_employee_messages_and_explicit_omissions(ctx):
    async with ctx.factory.begin() as session:
        other = Conversation(id=uuid4().hex, user_id=ctx.other.user_id)
        session.add(other)
        await session.flush()
        for index in range(7):
            session.add(Message(conversation_id=ctx.conversation, role="user", content=f"员工原文{index}", citations=[],
                created_at=datetime.now(timezone.utc) - timedelta(seconds=20-index)))
        large_id = uuid4().hex
        session.add_all([Message(id=large_id, conversation_id=ctx.conversation, role="user", content="长原文" * 1000, citations=[]),
            Message(conversation_id=ctx.conversation, role="assistant", content="模型猜测已重启并影响全公司", citations=[]),
            Message(conversation_id=other.id, role="user", content="其他员工私有问题", citations=[])])
    snapshot = await stored_context(ctx, await handoff(ctx))
    assert snapshot and snapshot["problem"] is None and snapshot["attempted_steps"] is None
    assert len(snapshot["messages"]) == 5 and snapshot["messages_omitted"]
    assert next(item for item in snapshot["messages"] if item["id"] == large_id) == {"id": large_id, "content": None, "omission": "too_long"}
    assert "模型猜测" not in str(snapshot) and "其他员工" not in str(snapshot)


@pytest.mark.asyncio
async def test_ten_handoff_retries_keep_one_immutable_snapshot_and_one_created_audit(ctx):
    tickets, _ = services(ctx)
    job = await running(ctx)
    results = await asyncio.gather(*[tickets.record_handoff(ctx.principal.user_id, ctx.conversation, job["id"],
        "explicit_manual_request", "trace", lease_token=job["lease_token"]) for _ in range(10)])
    assert len({item["id"] for item in results}) == 1
    identifier = results[0]["id"]
    snapshot = await stored_context(ctx, identifier)
    assert snapshot and snapshot["messages"]
    async with ctx.factory.begin() as session:
        message = await session.get(Message, snapshot["messages"][-1]["id"])
        message.content = "受控数据库改动，不应改写已记录快照"
    await tickets.record_handoff(ctx.principal.user_id, ctx.conversation, job["id"], "changed-reason", "new-trace", lease_token=job["lease_token"])
    assert await stored_context(ctx, identifier) == snapshot
    async with ctx.factory() as session:
        assert await session.scalar(select(func.count()).select_from(Escalation)) == 1
        assert await session.scalar(select(func.count()).select_from(BusinessAudit).where(BusinessAudit.entity_id == identifier)) == 1


@pytest.mark.asyncio
async def test_handoff_context_and_audit_roll_back_together_then_retry(ctx):
    tickets, _ = services(ctx)
    job = await running(ctx)
    def fail(mapper, connection, target):
        raise RuntimeError("controlled audit insert failure")
    event.listen(BusinessAudit, "before_insert", fail)
    try:
        with pytest.raises(RuntimeError, match="controlled audit"):
            await tickets.record_handoff(ctx.principal.user_id, ctx.conversation, job["id"], "explicit_manual_request", "trace", lease_token=job["lease_token"])
    finally:
        event.remove(BusinessAudit, "before_insert", fail)
    async with ctx.factory() as session:
        assert await session.scalar(select(func.count()).select_from(Escalation)) == 0
    saved = await tickets.record_handoff(ctx.principal.user_id, ctx.conversation, job["id"], "explicit_manual_request", "trace", lease_token=job["lease_token"])
    assert await stored_context(ctx, saved["id"])


@pytest.mark.asyncio
async def test_reaped_worker_failure_saves_the_same_bounded_context_contract(ctx):
    job = await running(ctx)
    async with ctx.factory.begin() as session:
        row = await session.get(ctx.models.ProductionRun, job["id"])
        row.deadline_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert await ctx.service.reap() == 1
    run = await ctx.service.get_run(ctx.principal, job["id"])
    snapshot = await stored_context(ctx, run["result"]["escalation_id"])
    assert run["status"] == "failed" and snapshot and snapshot["schema_version"] == 1
    assert snapshot["messages"][-1]["content"] == "VPN连接失败，请转人工"


@pytest.mark.asyncio
async def test_detail_and_processing_audit_are_owned_and_currently_authorized(ctx):
    tickets, _ = services(ctx)
    identifier = await handoff(ctx)
    operator = await support(ctx)
    actor = [Principal(ctx.principal.user_id, "Owner", "employee")]
    async with client(ctx, tickets, actor) as api:
        detail = await api.get(f"/api/v1/escalations/{identifier}")
        assert detail.status_code == 200 and detail.json()["context"]["messages"]
        actor[0] = Principal(ctx.other.user_id, "Other", "employee")
        assert (await api.get(f"/api/v1/escalations/{identifier}")).status_code == 404
        actor[0] = operator
        updated = await api.patch(f"/api/v1/escalations/{identifier}", json={"version": 1, "status": "in_progress", "assignee_id": operator.user_id})
        assert updated.status_code == 200
        detail = (await api.get(f"/api/v1/escalations/{identifier}")).json()
        assert detail["status"] == "in_progress" and detail["audit"][0]["actor_id"] == operator.user_id
        assert detail["audit"][0]["details"]["status"] == {"from": "pending", "to": "in_progress"}
        assert detail["audit"][0]["details"]["version"] == {"from": 1, "to": 2}
        async with ctx.factory.begin() as session:
            user = await session.get(User, operator.user_id)
            user.access_level = "employee"
        assert (await api.get(f"/api/v1/escalations/{identifier}")).status_code == 404
        assert (await api.patch(f"/api/v1/escalations/{identifier}", json={"version": 2, "status": "resolved"})).status_code == 403


@pytest.mark.asyncio
async def test_disabled_support_is_neither_listed_nor_assignable(ctx):
    tickets, _ = services(ctx)
    disabled = await support(ctx, enabled=False)
    operator = await support(ctx)
    async with ctx.factory() as session:
        with pytest.raises(HTTPException) as rejected:
            await tickets._assignee(session, disabled.user_id)
        assert rejected.value.status_code == 400
    async with client(ctx, tickets, [operator]) as api:
        response = await api.get("/api/v1/support/assignees")
        assert response.status_code == 200
        assert disabled.user_id not in [item["id"] for item in response.json()["users"]]


async def knowledge_reference(c, *, snapshot_access="employee"):
    from app.production.knowledge_models import KnowledgeRevision, KnowledgeSnapshot, KnowledgeSource, RevisionChunk
    source_id, revision = uuid4().hex, uuid4().hex
    content = "VPN错误E42：仅在证书过期时重新签发，等待5分钟；未过期则联系支持。"
    async with c.factory.begin() as session:
        session.add(KnowledgeRevision(id=revision, collection_name=revision, status="ready", document_count=1, chunk_count=1,
            embedding_model="test", embedding_revision="test", dimensions=2, pipeline_config={}, created_by=c.principal.user_id))
        session.add(KnowledgeSource(id=source_id, title="VPN原件", source_path="reference.md", content_hash="a"*64,
            content=content, access_level="employee", status="active"))
        await session.flush()
        session.add(KnowledgeSnapshot(revision_id=revision, document_id=source_id, title="VPN历史版本", source_path="reference.md",
            content=content, content_hash="a"*64, access_level=snapshot_access))
        session.add(RevisionChunk(revision_id=revision, document_id=source_id, chunk_index=0, content=content,
            source_title="VPN历史版本", source_path="reference.md", char_start=0, char_end=len(content), access_level="employee"))
        session.add(Message(conversation_id=c.conversation, role="assistant", content="模型答案不应进入交接快照", citations=[{
            "document_id": source_id, "index_revision": revision, "chunk_index": 0,
            "source_title": "UNTRUSTED_CITATION_TITLE", "excerpt": "UNTRUSTED_CITATION_EXCERPT"}]))
    return source_id, revision, content


@pytest.mark.asyncio
async def test_reference_text_is_resolved_from_currently_authorized_snapshot_on_each_read(ctx):
    from app.production.knowledge_models import KnowledgeSource
    tickets, _ = services(ctx)
    source_id, revision, content = await knowledge_reference(ctx)
    identifier = await handoff(ctx)
    snapshot = await stored_context(ctx, identifier)
    assert len(snapshot["citations"]) == 1
    assert set(snapshot["citations"][0]) == {"document_id", "index_revision", "chunk_index", "source_message_id"}
    assert "UNTRUSTED" not in str(snapshot) and "模型答案" not in str(snapshot)
    actor = [Principal(ctx.principal.user_id, "Owner", "employee")]
    async with client(ctx, tickets, actor) as api:
        detail = (await api.get(f"/api/v1/escalations/{identifier}")).json()
        citation = detail["context"]["citations"][0]
        assert citation["index_revision"] == revision and citation["excerpt"] == content
        async with ctx.factory.begin() as session:
            source = await session.get(KnowledgeSource, source_id)
            source.status = "inactive"
        hidden = (await api.get(f"/api/v1/escalations/{identifier}")).json()["context"]
        assert hidden["citations"] == [] and hidden["citations_unavailable"] == 1
        assert content not in str(hidden) and "VPN历史版本" not in str(hidden)
        assert await stored_context(ctx, identifier) == snapshot


@pytest.mark.asyncio
async def test_original_snapshot_permissions_cannot_be_relaxed_by_current_public_source(ctx):
    await knowledge_reference(ctx, snapshot_access="admin")
    snapshot = await stored_context(ctx, await handoff(ctx))
    assert snapshot["citations"] == [] and snapshot["citations_omitted"]


@pytest.mark.asyncio
async def test_legacy_record_does_not_invent_a_historical_context(ctx):
    tickets, _ = services(ctx)
    identifier = await handoff(ctx)
    async with ctx.factory.begin() as session:
        row = await session.get(Escalation, identifier)
        row.context = None
    async with client(ctx, tickets, [Principal(ctx.principal.user_id, "Owner", "employee")]) as api:
        response = await api.get(f"/api/v1/escalations/{identifier}")
        assert response.status_code == 200 and response.json()["context"] is None


@pytest.mark.asyncio
async def test_audit_cursor_is_bound_to_the_escalation_and_pages_without_gaps(ctx):
    tickets, _ = services(ctx)
    identifier = await handoff(ctx)
    actor = [await support(ctx)]
    stamp = datetime.now(timezone.utc)
    async with ctx.factory.begin() as session:
        for index in range(55):
            session.add(BusinessAudit(entity_type="escalation", entity_id=identifier, actor_id=actor[0].user_id,
                event_type="updated", details={"version": {"from": index+1, "to": index+2}, "private_unlisted": "PRIVATE_AUDIT_FIELD"},
                created_at=stamp+timedelta(seconds=index)))
        unrelated = BusinessAudit(entity_type="escalation", entity_id="other-escalation", actor_id=actor[0].user_id,
            event_type="updated", details={})
        session.add(unrelated)
        await session.flush()
        unrelated_id = unrelated.id
    async with client(ctx, tickets, actor) as api:
        first = (await api.get(f"/api/v1/escalations/{identifier}")).json()
        assert len(first["audit"]) == 50 and first["next_audit_cursor"]
        assert "PRIVATE_AUDIT_FIELD" not in str(first)
        second = (await api.get(f"/api/v1/escalations/{identifier}", params={"audit_cursor": first["next_audit_cursor"]})).json()
        assert len(second["audit"]) == 6 and second["next_audit_cursor"] is None
        assert len({row["id"] for row in first["audit"] + second["audit"]}) == 56
        assert (await api.get(f"/api/v1/escalations/{identifier}", params={"audit_cursor": unrelated_id})).status_code == 400
