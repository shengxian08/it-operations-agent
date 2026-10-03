"""T05 real disposable PG/Redis tests; all business text is synthetic."""
import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, event, func, select, update
from sqlalchemy.exc import OperationalError

from app.db.models import Ticket, TicketEvent, User
from app.production.business import CommentBody, TicketPatch
from app.production.business_models import BusinessAudit, ProductionTicket, ProductionTicketComment
from app.production.identity import Principal
from app.production.identity_models import IdentityAccount
from tests.production.test_runs import ctx  # noqa: F401
from tests.production.test_ticket_lookup_persistence import create_ticket, services, turn

STAMP = datetime(2026, 10, 3, 0, 0, tzinfo=timezone.utc)


def actor(value, role="employee"):
    return Principal(value.user_id, "Synthetic user", role, "test-session", "test-csrf")


async def setup(c):
    tickets, _ = services(c)
    number = await create_ticket(c, tickets)
    async with c.factory.begin() as session:
        await session.execute(update(User).where(User.id == c.other.user_id).values(access_level="support"))
        ticket = await session.scalar(select(ProductionTicket).where(ProductionTicket.ticket_number == number))
        await session.execute(delete(BusinessAudit).where(BusinessAudit.entity_id == ticket.id))
    return tickets, number, ticket.id, actor(c.other, "support")


async def comment(c, tid, content, visibility="public", offset=0, identifier=None):
    assert hasattr(ProductionTicketComment, "visibility"), "comments need durable visibility before returning progress"
    async with c.factory.begin() as session:
        row = ProductionTicketComment(id=identifier or uuid4().hex, ticket_id=tid, author_id=c.other.user_id,
            content=content, visibility=visibility, author_role="support", created_at=STAMP + timedelta(minutes=offset))
        session.add(row)
    return row


@pytest.mark.asyncio
async def test_no_records_is_found_without_invented_progress_time(ctx):
    tickets, number, _, _ = await setup(ctx)
    result = await tickets.get_ticket_status(ctx.principal.user_id, number)
    assert result.found and result.status == "pending"
    assert result.latest_update == "暂无可见处理记录。"
    assert result.updated_at is None and result.update_kind is None


@pytest.mark.asyncio
async def test_new_public_comment_is_actual_latest_progress_with_time(ctx):
    tickets, number, _, staff = await setup(ctx)
    written = await tickets.add_comment(staff, number, 1, "已定位合成 VPN 配置问题，正在修复。")
    result = await tickets.get_ticket_status(ctx.principal.user_id, number)
    assert "合成 VPN 配置问题" in result.latest_update
    assert result.updated_at == written["created_at"]
    assert result.update_kind == "support_reply"
    assert result.progress_source["source_id"] == written["id"]


@pytest.mark.asyncio
async def test_latest_visible_record_and_tie_are_selected_without_internal_activity_time(ctx):
    tickets, number, tid, staff = await setup(ctx)
    await comment(ctx, tid, "较早公开记录", offset=1)
    visible = await comment(ctx, tid, "最新公开记录", offset=2, identifier="z-public")
    await comment(ctx, tid, "同时间较小 ID", offset=2, identifier="a-public")
    hidden = await comment(ctx, tid, "CONTROLLED_INTERNAL_MARKER", "internal", offset=3)
    await comment(ctx, tid, "CONTROLLED_UNCLASSIFIED_MARKER", "unclassified", offset=4)
    result = await tickets.get_ticket_status(ctx.principal.user_id, number)
    assert result.latest_update == "最新公开记录" and result.updated_at == visible.created_at
    assert "CONTROLLED" not in str(await tickets.ticket_detail(actor(ctx.principal), number))
    internal = await tickets.get_ticket_status(staff.user_id, number)
    assert internal.latest_update == "CONTROLLED_UNCLASSIFIED_MARKER"
    assert internal.progress_source["visibility"] == "unclassified"
    async with ctx.factory.begin() as session:
        await session.execute(delete(ProductionTicketComment).where(ProductionTicketComment.visibility == "unclassified"))
    assert (await tickets.get_ticket_status(staff.user_id, number)).updated_at == hidden.created_at


@pytest.mark.asyncio
async def test_unknown_audits_and_raw_details_never_become_public_progress(ctx):
    tickets, number, tid, staff = await setup(ctx)
    async with ctx.factory.begin() as session:
        session.add_all([BusinessAudit(entity_type="ticket", entity_id=tid, actor_id=staff.user_id,
            event_type="updated", details={"status": {"from": "pending", "to": "in_progress"}, "private": "CONTROLLED_SECRET"}, created_at=STAMP),
            BusinessAudit(entity_type="ticket", entity_id=tid, actor_id=staff.user_id, event_type="commented",
                details={"private": "CONTROLLED_SECRET"}, created_at=STAMP + timedelta(hours=1))])
    result = await tickets.get_ticket_status(ctx.principal.user_id, number)
    assert result.updated_at == STAMP and "处理中" in result.latest_update
    detail = await tickets.ticket_detail(actor(ctx.principal), number)
    assert "CONTROLLED_SECRET" not in str(detail) and len(detail["audit"]) == 1


@pytest.mark.asyncio
async def test_empty_internal_and_unclassified_do_not_make_employee_progress(ctx):
    tickets, number, tid, _ = await setup(ctx)
    for body, visibility in [("  \n\t", "public"), ("仅内部", "internal"), ("尚未核对", "unclassified")]:
        await comment(ctx, tid, body, visibility)
    result = await tickets.get_ticket_status(ctx.principal.user_id, number)
    assert result.latest_update == "暂无可见处理记录。" and result.updated_at is None


@pytest.mark.asyncio
async def test_employee_cannot_write_internal_and_current_role_is_used_for_detail_and_write(ctx):
    tickets, number, tid, staff = await setup(ctx)
    assert hasattr(ProductionTicketComment, "visibility")
    with pytest.raises(HTTPException) as denied:
        await tickets.add_comment(actor(ctx.principal), number, 1, "不可写入", visibility="internal")
    assert denied.value.status_code == 403
    async with ctx.factory() as session:
        assert await session.scalar(select(func.count()).select_from(ProductionTicketComment)) == 0
    await tickets.add_comment(staff, number, 1, "CONTROLLED_INTERNAL", visibility="internal")
    async with ctx.factory.begin() as session:
        await session.execute(update(User).where(User.id == staff.user_id).values(access_level="employee"))
    for operation in [tickets.ticket_detail(staff, number), tickets.add_comment(staff, number, 2, "stale staff")]:
        with pytest.raises(HTTPException) as error:
            await operation
        assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_original_internal_permission_and_current_visibility_both_restrict_replay(ctx):
    tickets, number, tid, staff = await setup(ctx)
    row = await comment(ctx, tid, "CONTROLLED_REPLAY_SECRET", "internal")
    ctx.principal = staff
    ctx.conversation = (await ctx.service.create_conversation(staff, "Synthetic support query"))["id"]
    first = await turn(ctx, number)
    assert "CONTROLLED_REPLAY_SECRET" in first["result"]["answer"]
    async with ctx.factory.begin() as session:
        await session.execute(update(User).where(User.id == staff.user_id).values(access_level="employee"))
        await session.execute(update(ProductionTicket).where(ProductionTicket.id == tid).values(user_id=staff.user_id))
        await session.execute(update(ProductionTicketComment).where(ProductionTicketComment.id == row.id).values(visibility="public"))
    views = [await ctx.service.get_run(staff, first["id"]), await ctx.service.events(staff, first["id"]),
             await ctx.service.conversation_runs(staff, ctx.conversation), await ctx.service.messages(staff, ctx.conversation),
             await ctx.service.history(first["id"])]
    assert all("CONTROLLED_REPLAY_SECRET" not in str(view) for view in views)
    assert (await ctx.service.get_run(staff, first["id"]))["result"]["access_redacted"] is True
    # A genuinely new query may use current public permission, but never upgrades the old snapshot.
    ctx.principal = actor(staff)  # The next HTTP request obtains the current employee principal.
    assert "CONTROLLED_REPLAY_SECRET" in (await turn(ctx, number))["result"]["answer"]


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["internal", "delete", "edit"])
async def test_current_source_restriction_deletion_or_edit_hides_old_answer(ctx, change):
    tickets, number, tid, _ = await setup(ctx)
    row = await comment(ctx, tid, "CONTROLLED_OLD_PUBLIC")
    first = await turn(ctx, number)
    assert "CONTROLLED_OLD_PUBLIC" in first["result"]["answer"]
    async with ctx.factory.begin() as session:
        if change == "delete":
            await session.execute(delete(ProductionTicketComment).where(ProductionTicketComment.id == row.id))
        else:
            await session.execute(update(ProductionTicketComment).where(ProductionTicketComment.id == row.id).values(
                **({"visibility": "internal"} if change == "internal" else {"content": "已修正的公开记录"})))
    assert "CONTROLLED_OLD_PUBLIC" not in str(await ctx.service.get_run(ctx.principal, first["id"]))
    assert "CONTROLLED_OLD_PUBLIC" not in str(await ctx.service.messages(ctx.principal, ctx.conversation))


@pytest.mark.asyncio
async def test_model_history_never_receives_prior_ticket_tool_answer(ctx):
    tickets, number, tid, staff = await setup(ctx)
    await comment(ctx, tid, "CONTROLLED_MODEL_HISTORY_INTERNAL", "internal")
    ctx.principal, ctx.conversation = staff, (await ctx.service.create_conversation(staff, "Support"))["id"]
    first = await turn(ctx, number)
    assert "CONTROLLED_MODEL_HISTORY_INTERNAL" in first["result"]["answer"]
    assert "CONTROLLED_MODEL_HISTORY_INTERNAL" not in str(await ctx.service.history(first["id"]))


@pytest.mark.asyncio
async def test_current_disabled_account_rejects_existing_ticket_result_replay(ctx):
    tickets, number, tid, _ = await setup(ctx)
    first = await turn(ctx, number)
    async with ctx.factory.begin() as session:
        await session.execute(update(IdentityAccount).where(IdentityAccount.user_id == ctx.principal.user_id).values(enabled=False))
    with pytest.raises(HTTPException) as error:
        await ctx.service.get_run(ctx.principal, first["id"])
    assert error.value.status_code == 403


def test_comment_schema_rejects_unknown_visibility_and_unclassified_new_comment():
    for visibility in ("everyone", "unclassified"):
        with pytest.raises(ValueError):
            CommentBody(version=1, content="Synthetic", visibility=visibility)


@pytest.mark.asyncio
async def test_visible_committed_update_is_requeried_without_side_effects(ctx):
    tickets, number, tid, staff = await setup(ctx)
    first = await turn(ctx, number)
    assert first["result"]["final_state"] == "ticket_status"
    updated = await tickets.update_ticket(staff, number, TicketPatch(version=1, status="in_progress"))
    written = await tickets.add_comment(staff, number, updated["version"], "合成问题已定位")
    second = await turn(ctx, "进度呢")
    assert "合成问题已定位" in second["result"]["answer"] and "UTC" in second["result"]["answer"]
    assert second["result"]["ticket_lookup"]["progress_source"]["source_id"] == written["id"]
    assert len([e for e in await ctx.service.events(ctx.principal, second["id"]) if e["type"] == "final"]) == 1


@pytest.mark.asyncio
async def test_legacy_repository_requires_public_event_and_returns_record_time(ctx):
    from app.repositories.tickets import TicketRepository
    async with ctx.factory.begin() as session:
        ticket = Ticket(id=uuid4().hex, ticket_number="IT-2026-0901", user_id=ctx.principal.user_id, title="Synthetic",
                        category="network", priority="medium", description="Synthetic", status="pending", attempted_steps=[])
        session.add(ticket)
        await session.flush()
        session.add_all([TicketEvent(ticket_id=ticket.id, event_type="updated", details={"visibility": "public", "summary": "合成公开处理记录"}, created_at=STAMP),
            TicketEvent(ticket_id=ticket.id, event_type="updated", details={"visibility": "internal", "summary": "CONTROLLED_LEGACY_INTERNAL"}, created_at=STAMP + timedelta(minutes=1)),
            TicketEvent(ticket_id=ticket.id, event_type="unknown", details={"summary": "CONTROLLED_UNKNOWN_SUMMARY"}, created_at=STAMP + timedelta(minutes=2))])
    repository = TicketRepository(ctx.factory)
    result = await repository.get_status_for_user(ctx.principal.user_id, ticket.ticket_number)
    assert result.latest_update == "合成公开处理记录" and result.updated_at == STAMP
    assert await repository.get_status_for_user(ctx.other.user_id, ticket.ticket_number) is None


@pytest.mark.asyncio
async def test_comment_classification_is_atomic_scoped_and_never_relabels_author(ctx):
    tickets, number, tid, staff = await setup(ctx)
    row = await comment(ctx, tid, "历史合成记录", "unclassified")
    with pytest.raises(HTTPException) as denied:
        await tickets.classify_comment(actor(ctx.principal), number, row.id, 1, "public")
    assert denied.value.status_code == 403
    result = await tickets.classify_comment(staff, number, row.id, 1, "public")
    assert result["version"] == 2
    assert "历史合成记录" in str(await tickets.ticket_detail(actor(ctx.principal), number))
    with pytest.raises(HTTPException) as conflict:
        await tickets.classify_comment(staff, number, row.id, 1, "internal")
    assert conflict.value.status_code == 409
    async with ctx.factory() as session:
        assert (await session.get(ProductionTicketComment, row.id)).author_role == "support"
        assert await session.scalar(select(func.count()).select_from(BusinessAudit).where(
            BusinessAudit.event_type == "comment_visibility_changed")) == 1


@pytest.mark.asyncio
async def test_unknown_legacy_comment_defaults_to_unclassified_and_does_not_leak_activity_time(ctx):
    tickets, number, tid, staff = await setup(ctx)
    async with ctx.factory.begin() as session:
        await session.execute(update(ProductionTicket).where(ProductionTicket.id == tid).values(created_at=STAMP, updated_at=STAMP + timedelta(hours=2)))
        old = ProductionTicketComment(ticket_id=tid, author_id=staff.user_id, content="CONTROLLED_OLD_UNCLASSIFIED", created_at=STAMP + timedelta(hours=2))
        session.add(old)
        await session.flush()
        assert old.visibility == "unclassified" and old.author_role is None
    detail = await tickets.ticket_detail(actor(ctx.principal), number)
    assert detail["comments"] == [] and detail["updated_at"] == STAMP
    assert (await tickets.get_ticket_status(ctx.principal.user_id, number)).updated_at is None


@pytest.mark.asyncio
async def test_concurrent_comments_one_commit_and_failed_audit_rolls_back_before_retry(ctx):
    tickets, number, tid, staff = await setup(ctx)
    engine = ctx.factory.kw["bind"].sync_engine
    def fail_audit(_conn, _cursor, statement, _params, _context, _many):
        if statement.startswith("INSERT INTO production_business_audits"):
            raise OperationalError(statement, None, RuntimeError("controlled audit write failure"))
    event.listen(engine, "before_cursor_execute", fail_audit)
    try:
        with pytest.raises(OperationalError):
            await tickets.add_comment(staff, number, 1, "不得部分保存", "internal")
    finally:
        event.remove(engine, "before_cursor_execute", fail_audit)
    async with ctx.factory() as session:
        assert await session.scalar(select(func.count()).select_from(ProductionTicketComment)) == 0
        assert (await session.get(ProductionTicket, tid)).version == 1
    results = await asyncio.gather(*[tickets.add_comment(staff, number, 1, f"已提交公开记录 {i}") for i in range(6)], return_exceptions=True)
    assert len([value for value in results if isinstance(value, dict)]) == 1
    assert len([value for value in results if isinstance(value, HTTPException) and value.status_code == 409]) == 5
    committed = next(value for value in results if isinstance(value, dict))
    current = await tickets.get_ticket_status(ctx.principal.user_id, number)
    assert current.latest_update == committed["content"] and current.updated_at == committed["created_at"]


@pytest.mark.asyncio
async def test_actual_comment_query_database_error_is_unavailable_not_empty_or_not_found(ctx):
    tickets, number, tid, _ = await setup(ctx)
    engine = ctx.factory.kw["bind"].sync_engine
    def fail_comment_query(_conn, _cursor, statement, _params, _context, _many):
        if "FROM production_ticket_comments" in statement:
            raise OperationalError(statement, None, RuntimeError("CONTROLLED_PRIVATE_DB_ERROR"))
    event.listen(engine, "before_cursor_execute", fail_comment_query)
    try:
        result = await turn(ctx, number)
    finally:
        event.remove(engine, "before_cursor_execute", fail_comment_query)
    assert result["result"]["ticket_lookup"]["outcome"] == "unavailable"
    assert result["result"]["final_state"] == "handoff" and result["result"]["escalation_id"]
    assert all(word not in str(result) for word in ("暂无可见处理", "未找到", "CONTROLLED_PRIVATE"))


@pytest.mark.asyncio
async def test_long_and_control_characters_are_bounded_and_empty_batches_do_not_hide_older_record(ctx):
    tickets, number, tid, _ = await setup(ctx)
    await comment(ctx, tid, "实际记录\x1b[31m\u202e " + "长" * 900)
    async with ctx.factory.begin() as session:
        session.add_all([ProductionTicketComment(ticket_id=tid, author_id=ctx.principal.user_id,
            content=" \t\n", visibility="public", created_at=STAMP + timedelta(minutes=1)) for _ in range(51)])
    result = await tickets.get_ticket_status(ctx.principal.user_id, number)
    assert len(result.latest_update) < 630 and "后续内容请查看" in result.latest_update
    assert "\x1b" not in result.latest_update and "\u202e" not in result.latest_update
    assert result.updated_at == STAMP


@pytest.mark.asyncio
async def test_actual_http_replay_and_write_visibility_follow_current_principal(ctx):
    import json
    from types import SimpleNamespace
    import httpx
    from fastapi import FastAPI
    from app.production.business import router as business_router
    from app.production.identity import require_principal
    from app.production.runs import router as runs_router
    tickets, number, tid, staff = await setup(ctx)
    current = actor(ctx.principal)
    app = FastAPI()
    app.state.ticket_service, app.state.run_service = tickets, ctx.service
    async def authenticated():
        return current
    async def session_principal(_session):
        return current
    app.dependency_overrides[require_principal] = authenticated
    app.state.identity_service = SimpleNamespace(principal=session_principal)
    app.include_router(business_router)
    app.include_router(runs_router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        url = f"/api/v1/tickets/{number}/comments"
        assert (await client.post(url, json={"version": 1, "content": "test", "visibility": "internal"})).status_code == 403
        assert (await client.post(url, json={"version": 1, "content": "test", "visibility": "unclassified"})).status_code == 422
        current = staff
        response = await client.post(url, json={"version": 1, "content": "CONTROLLED_HTTP_INTERNAL", "visibility": "internal"})
        assert response.status_code == 200 and response.json()["visibility"] == "internal"
        ctx.principal = staff
        ctx.conversation = (await ctx.service.create_conversation(staff, "HTTP"))["id"]
        first = await turn(ctx, number)
        assert "CONTROLLED_HTTP_INTERNAL" in (await client.get(f"/api/v1/runs/{first['id']}/events")).text
        async with ctx.factory() as session:
            original = await session.get(ctx.models.ProductionRun, first["id"])
            retry_body = {"content": number, "client_message_id": original.client_message_id}
            retry_headers = {"Idempotency-Key": original.idempotency_key}
        async with ctx.factory.begin() as session:
            await session.execute(update(User).where(User.id == staff.user_id).values(access_level="employee"))
        current = actor(staff)
        for path in (f"/runs/{first['id']}", f"/runs/{first['id']}/events", f"/conversations/{ctx.conversation}/runs", f"/conversations/{ctx.conversation}/messages"):
            replay = await client.get("/api/v1" + path)
            assert replay.status_code == 200 and "CONTROLLED_HTTP_INTERNAL" not in replay.text
        replay = await client.get(f"/api/v1/runs/{first['id']}/events")
        assert replay.text.count("event: final\n") == 1 and '"access_redacted":true' in replay.text
        assert "CONTROLLED_HTTP_INTERNAL" not in json.dumps(await ctx.service.cancel(staff, first["id"]), ensure_ascii=False)
        retry = await client.post(f"/api/v1/conversations/{ctx.conversation}/runs", json=retry_body, headers=retry_headers)
        assert retry.status_code == 202 and retry.json()["id"] == first["id"]
        assert retry.json()["result"]["access_redacted"] is True and "CONTROLLED_HTTP_INTERNAL" not in retry.text
        async with ctx.factory() as session:
            persisted = await session.get(ctx.models.ProductionRun, first["id"])
            assert "CONTROLLED_HTTP_INTERNAL" in persisted.result["answer"]  # Filtering must not rewrite original evidence.


@pytest.mark.asyncio
async def test_role_demotion_while_waiting_for_ticket_lock_cannot_return_internal_progress(ctx):
    tickets, number, tid, staff = await setup(ctx)
    await comment(ctx, tid, "CONTROLLED_WAITING_INTERNAL", "internal")
    reached_lock = asyncio.Event()
    engine = ctx.factory.kw["bind"].sync_engine
    def observe(_conn, _cursor, statement, _params, _context, _many):
        if "FROM production_tickets" in statement and "FOR UPDATE" in statement:
            reached_lock.set()
    async with ctx.factory.begin() as blocker:
        await blocker.scalar(select(ProductionTicket).where(ProductionTicket.id == tid).with_for_update())
        event.listen(engine, "before_cursor_execute", observe)
        task = asyncio.create_task(tickets.get_ticket_status(staff.user_id, number))
        try:
            await asyncio.wait_for(reached_lock.wait(), 5)
            async with ctx.factory.begin() as session:
                await session.execute(update(User).where(User.id == staff.user_id).values(access_level="employee"))
        finally:
            event.remove(engine, "before_cursor_execute", observe)
    result = await asyncio.wait_for(task, 5)
    assert result.found is False and "CONTROLLED_WAITING_INTERNAL" not in str(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["add_internal", "classify"])
async def test_staff_demotion_while_waiting_for_own_ticket_lock_cannot_write_restricted_operation(ctx, operation):
    tickets, number, tid, staff = await setup(ctx)
    old = await comment(ctx, tid, "未分类记录", "unclassified")
    async with ctx.factory.begin() as session:
        await session.execute(update(ProductionTicket).where(ProductionTicket.id == tid).values(user_id=staff.user_id))
    reached_lock = asyncio.Event()
    engine = ctx.factory.kw["bind"].sync_engine
    def observe(_conn, _cursor, statement, _params, _context, _many):
        if "FROM production_tickets" in statement and "FOR UPDATE" in statement:
            reached_lock.set()
    async with ctx.factory.begin() as blocker:
        await blocker.scalar(select(ProductionTicket).where(ProductionTicket.id == tid).with_for_update())
        event.listen(engine, "before_cursor_execute", observe)
        action = tickets.add_comment(staff, number, 1, "不得保存", "internal") if operation == "add_internal" else tickets.classify_comment(staff, number, old.id, 1, "public")
        task = asyncio.create_task(action)
        try:
            await asyncio.wait_for(reached_lock.wait(), 5)
            async with ctx.factory.begin() as session:
                await session.execute(update(User).where(User.id == staff.user_id).values(access_level="employee"))
        finally:
            event.remove(engine, "before_cursor_execute", observe)
    with pytest.raises(HTTPException) as denied:
        await asyncio.wait_for(task, 5)
    assert denied.value.status_code == 403
    async with ctx.factory() as session:
        assert (await session.get(ProductionTicket, tid)).version == 1
        assert (await session.get(ProductionTicketComment, old.id)).visibility == "unclassified"
        assert await session.scalar(select(func.count()).select_from(ProductionTicketComment)) == 1
