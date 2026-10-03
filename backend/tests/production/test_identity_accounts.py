import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import select

from app.db.models import User
from app.production.identity import IdentityService, Principal
from app.production.identity_models import IdentityAccount
from tests.production.test_runs import ctx


async def disabled_run_record(ctx, run_id):
    # Disabled users cannot replay data. Inspect the owned disposable schema to retain
    # the original terminal-state assertions without granting user read access.
    from app.production.run_models import ProductionRun
    with pytest.raises(HTTPException) as denied:
        await ctx.service.get_run(ctx.other, run_id)
    assert denied.value.status_code == 403
    async with ctx.factory() as session:
        run = await session.get(ProductionRun, run_id)
        return ctx.service.run_payload(run)


@pytest_asyncio.fixture
async def accounts(ctx):
    assert hasattr(IdentityAccount, "role_override"), "local administrative role override is missing"
    ctx.service.settings.session_secret = "test-only-secret-" * 3
    ctx.service.settings.oidc_http_timeout_seconds = 1
    ctx.service.settings.oidc_allowed_roles = ("employee", "support", "admin")
    ctx.service.settings.session_cookie_name = "it_assistant_session"
    ctx.service.settings.public_base_url = "https://assistant.example"
    async with ctx.factory.begin() as session:
        user = await session.get(User, ctx.principal.user_id)
        user.access_level = "admin"
    identity = IdentityService(ctx.service.settings, ctx.service.redis, ctx.factory)
    yield SimpleNamespace(ctx=ctx, identity=identity, admin=Principal(ctx.principal.user_id, "Admin", "admin"))
    await identity.close()


@pytest.mark.asyncio
async def test_admin_accounts_versioned_role_override_and_audit(accounts):
    from app.production.business_models import BusinessAudit
    service = accounts.identity
    other = accounts.ctx.other.user_id
    listing = await service.accounts(accounts.admin, limit=1)
    assert listing["items"] and listing["next_cursor"]
    assert set(listing["items"][0]) == {"id", "display_name", "role", "enabled", "version"}
    result = await service.update_account(accounts.admin, other, expected_version=1, role="support", enabled=True)
    assert result["role"] == "support" and result["version"] == 2
    with pytest.raises(HTTPException) as error:
        await service.update_account(accounts.admin, other, expected_version=1, role="employee")
    assert error.value.status_code == 409
    async with accounts.ctx.factory() as session:
        account = await session.scalar(select(IdentityAccount).where(IdentityAccount.user_id == other))
        assert account.role_override == "support"
        audit = await session.scalar(select(BusinessAudit).where(BusinessAudit.entity_type == "identity_account"))
        assert audit.actor_id == accounts.admin.user_id and audit.event_type == "updated"


@pytest.mark.asyncio
async def test_last_enabled_admin_is_protected_under_concurrent_demotions(accounts):
    service, ctx = accounts.identity, accounts.ctx
    other = ctx.other.user_id
    await service.update_account(accounts.admin, other, expected_version=1, role="admin")
    results = await asyncio.gather(
        service.update_account(accounts.admin, accounts.admin.user_id, expected_version=1, role="employee"),
        service.update_account(Principal(other, "Admin2", "admin"), other, expected_version=2, enabled=False),
        return_exceptions=True)
    assert sum(isinstance(result, dict) for result in results) == 1
    assert any(isinstance(result, HTTPException) and result.status_code == 409 and result.detail == "last_enabled_admin" for result in results)
    async with ctx.factory() as session:
        remaining = list(await session.scalars(select(User.id).join(IdentityAccount, IdentityAccount.user_id == User.id)
            .where(User.access_level == "admin", IdentityAccount.enabled.is_(True))))
        assert len(remaining) == 1


@pytest.mark.asyncio
async def test_disable_revokes_session_and_queued_run_in_same_transaction(accounts):
    service, ctx = accounts.identity, accounts.ctx
    session_id = uuid4().hex
    await service.redis.set(service._key("session", session_id), json.dumps({"user_id": ctx.other.user_id, "csrf_token": "csrf"}))
    assert (await service.principal(session_id)).user_id == ctx.other.user_id
    conversation = await ctx.service.create_conversation(ctx.other)
    queued = await ctx.service.enqueue(ctx.other, conversation["id"], "help", uuid4().hex, uuid4().hex)
    await service.update_account(accounts.admin, ctx.other.user_id, expected_version=1, enabled=False)
    with pytest.raises(HTTPException) as error:
        await service.principal(session_id)
    assert error.value.status_code == 401
    run = await disabled_run_record(ctx, queued["id"])
    assert run["status"] == "failed" and run["result"]["error"] == "account_access_changed"
    assert await ctx.service.claim("worker") is None


@pytest.mark.asyncio
async def test_demotion_fences_running_worker_and_current_database_role_controls_checks(accounts):
    service, ctx = accounts.identity, accounts.ctx
    await service.update_account(accounts.admin, ctx.other.user_id, expected_version=1, role="support")
    conversation = await ctx.service.create_conversation(ctx.other)
    support = Principal(ctx.other.user_id, "Support", "support")
    queued = await ctx.service.enqueue(support, conversation["id"], "help", uuid4().hex, uuid4().hex)
    job = await ctx.service.claim("worker")
    await service.update_account(accounts.admin, ctx.other.user_id, expected_version=2, role="employee")
    assert not await ctx.service.heartbeat(job["id"], job["lease_token"])
    assert not await ctx.service.append_event(job["id"], job["lease_token"], "citations", {"citations": [{"document_id": "restricted"}]})
    assert not await ctx.service.finalize(job["id"], job["lease_token"], "completed", {"answer": "restricted content"})
    assert (await ctx.service.get_run(ctx.other, queued["id"]))["status"] == "failed"


@pytest.mark.asyncio
async def test_claim_and_heartbeat_fail_closed_when_identity_was_disabled_directly(accounts):
    from app.production.run_models import ProductionRun
    service, ctx = accounts.identity, accounts.ctx
    conversation = await ctx.service.create_conversation(ctx.other)
    queued = await ctx.service.enqueue(ctx.other, conversation["id"], "help", uuid4().hex, uuid4().hex)
    async with ctx.factory.begin() as session:
        account = await session.scalar(select(IdentityAccount).where(IdentityAccount.user_id == ctx.other.user_id))
        account.enabled = False
    assert await ctx.service.claim("worker") is None
    assert (await disabled_run_record(ctx, queued["id"]))["status"] == "failed"


@pytest.mark.asyncio
async def test_account_disable_and_worker_failure_do_not_deadlock_on_user_foreign_keys(accounts):
    from app.production.run_models import ProductionRun
    service, ctx = accounts.identity, accounts.ctx
    conversation = await ctx.service.create_conversation(ctx.other)
    queued = await ctx.service.enqueue(ctx.other, conversation["id"], "help", uuid4().hex, uuid4().hex)
    job = await ctx.service.claim("worker")
    run_locked, admin_updating = asyncio.Event(), asyncio.Event()
    original = service._invalidate_runs
    async def signal_then_invalidate(session, user_id):
        admin_updating.set()
        await original(session, user_id)
    service._invalidate_runs = signal_then_invalidate
    async def failure():
        async with ctx.factory.begin() as session:
            run = await session.scalar(select(ProductionRun).where(ProductionRun.id == queued["id"]).with_for_update())
            run_locked.set()
            await admin_updating.wait()
            await ctx.service._finish(session, run, "failed", {"answer": "failed", "final_state": "handoff", "error": "processing_failed"})
    async def disable():
        await run_locked.wait()
        await service.update_account(accounts.admin, ctx.other.user_id, expected_version=1, enabled=False)
    await asyncio.wait_for(asyncio.gather(failure(), disable()), 10)
    assert (await disabled_run_record(ctx, queued["id"]))["status"] == "failed"


@pytest.mark.asyncio
async def test_queued_run_does_not_gain_new_privileges_when_account_is_promoted(accounts):
    service, ctx = accounts.identity, accounts.ctx
    conversation = await ctx.service.create_conversation(ctx.other)
    queued = await ctx.service.enqueue(ctx.other, conversation["id"], "help", uuid4().hex, uuid4().hex)
    await service.update_account(accounts.admin, ctx.other.user_id, expected_version=1, role="support")
    job = await ctx.service.claim("worker")
    assert job["id"] == queued["id"] and job["user_access_level"] == "employee"


@pytest.mark.asyncio
async def test_accounts_http_rejects_employee_and_missing_csrf_then_revokes_disabled_session(accounts):
    import httpx
    from fastapi import FastAPI
    from app.production.auth import router
    service, ctx = accounts.identity, accounts.ctx
    application = FastAPI()
    application.state.identity_service = service
    application.include_router(router)
    ids = {}
    for user_id in (accounts.admin.user_id, ctx.other.user_id):
        session_id = uuid4().hex
        ids[user_id] = session_id
        await service.redis.set(service._key("session", session_id), json.dumps({"user_id": user_id, "csrf_token": "csrf"}))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url=service.settings.public_base_url) as client:
        client.cookies.set(service.settings.session_cookie_name, ids[ctx.other.user_id])
        assert (await client.get("/api/v1/admin/accounts")).status_code == 403
        client.cookies.set(service.settings.session_cookie_name, ids[accounts.admin.user_id])
        listing = await client.get("/api/v1/admin/accounts?limit=1")
        assert listing.status_code == 200 and listing.json()["next_cursor"]
        body = {"expected_version": 1, "enabled": False}
        target = f"/api/v1/admin/accounts/{ctx.other.user_id}"
        assert (await client.patch(target, json=body)).status_code == 403
        response = await client.patch(target, json=body, headers={"Origin": service.settings.public_base_url, "X-CSRF-Token": "csrf"})
        assert response.status_code == 200 and response.json()["enabled"] is False
        client.cookies.set(service.settings.session_cookie_name, ids[ctx.other.user_id])
        assert (await client.get("/api/v1/me")).status_code == 401
