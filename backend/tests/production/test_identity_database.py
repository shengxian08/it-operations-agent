import json
import os
import time
from uuid import uuid4
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
from fastapi import FastAPI, HTTPException
from redis.asyncio import Redis
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import Base, User
from app.production.auth import router
from app.production.identity import IdentityService
from app.production.identity_models import IdentityAccount
from tests.production.test_identity import oidc_provider


@pytest.mark.asyncio
async def test_oidc_http_login_provisions_verified_subject_rotates_cookie_and_logout_revokes_session():
    config, signing_key, provider = oidc_provider()
    schema = "identity_test_" + uuid4().hex
    admin = create_async_engine(os.environ["TEST_DATABASE_URL"])
    async with admin.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"], connect_args={"server_settings": {"search_path": schema}})
    factory = async_sessionmaker(engine, expire_on_commit=False)
    redis = Redis.from_url(os.environ["TEST_REDIS_URL"], decode_responses=True)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    service = IdentityService(config, redis, factory)
    await service.http.aclose()
    nonce = ""
    original_transport = provider._transport
    async def handle(request):
        if request.url.path.endswith("/token"):
            claims = {"iss": config.oidc_issuer, "aud": config.oidc_client_id, "sub": "subject-" + schema,
                      "exp": int(time.time()) + 300, "iat": int(time.time()), "nonce": nonce,
                      "realm_access": {"roles": ["employee"]}, "name": "企业员工"}
            token = jwt.encode(claims, signing_key, algorithm="RS256", headers={"kid": "company-key"})
            return httpx.Response(200, json={"id_token": token})
        return await original_transport.handle_async_request(request)
    service.http = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    application = FastAPI()
    application.state.identity_service = service
    application.include_router(router)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url=config.public_base_url, follow_redirects=False) as client:
            assert (await client.get("/api/v1/me")).status_code == 401
            login = await client.get("/api/v1/auth/login?return_to=/conversations")
            assert login.status_code == 302
            query = parse_qs(urlparse(login.headers["location"]).query)
            nonce = query["nonce"][0]
            callback = await client.get("/api/v1/auth/callback", params={"code": "opaque-code", "state": query["state"][0]})
            assert callback.status_code == 302 and callback.headers["location"] == "/conversations"
            cookie = callback.headers["set-cookie"]
            assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=lax" in cookie
            session_id = client.cookies.get(config.session_cookie_name)
            me = (await client.get("/api/v1/me")).json()
            assert me["display_name"] == "企业员工" and me["role"] == "employee"
            async with factory() as session:
                assert await session.scalar(select(func.count()).select_from(User)) == 1
                account = await session.scalar(select(IdentityAccount))
                assert account.user_id == me["id"]
            async with factory.begin() as session:
                account = await session.scalar(select(IdentityAccount))
                account.role_override = "support"
                user = await session.get(User, account.user_id)
                user.access_level = "support"
            # The provider still asserts employee; a local administrative override
            # remains authoritative on subsequent verified OIDC logins.
            login = await client.get("/api/v1/auth/login")
            query = parse_qs(urlparse(login.headers["location"]).query)
            nonce = query["nonce"][0]
            callback = await client.get("/api/v1/auth/callback", params={"code": "second-code", "state": query["state"][0]})
            assert callback.status_code == 302
            with pytest.raises(HTTPException):
                await service.principal(session_id)
            session_id = client.cookies.get(config.session_cookie_name)
            me = (await client.get("/api/v1/me")).json()
            assert me["role"] == "support"
            assert (await client.post("/api/v1/auth/logout")).status_code == 403
            headers = {"Origin": config.public_base_url, "X-CSRF-Token": me["csrf_token"]}
            assert (await client.post("/api/v1/auth/logout", headers=headers)).status_code == 204
            with pytest.raises(HTTPException) as error:
                await service.principal(session_id)
            assert error.value.status_code == 401
            assert (await client.get("/api/v1/me")).status_code == 401
    finally:
        await service.close()
        await provider.aclose()
        await redis.aclose()
        await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()
