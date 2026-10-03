"""OIDC Authorization Code + PKCE and opaque, server-side sessions."""
import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Annotated, Any
from urllib.parse import urlencode, urlsplit

import httpx
import jwt
from fastapi import Depends, HTTPException, Request
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert

from app.db.models import User
from app.production.identity_models import IdentityAccount


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: str
    display_name: str
    role: str
    session_id: str = field(default="", repr=False)
    csrf_token: str = field(default="", repr=False)


@dataclass(frozen=True, slots=True)
class LoginFlow:
    url: str
    browser_token: str


def _secret(value: Any) -> str:
    return value.get_secret_value() if hasattr(value, "get_secret_value") else str(value or "")


def _text(value: Any) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def _unauthorized() -> HTTPException:
    return HTTPException(401, detail="authentication_required")


class IdentityService:
    def __init__(self, settings, redis, session_factory) -> None:
        self.settings = settings
        self.redis = redis
        self.session_factory = session_factory
        self.http = httpx.AsyncClient(timeout=settings.oidc_http_timeout_seconds, follow_redirects=False)
        self._metadata: dict[str, Any] | None = None
        self._metadata_until = 0.0
        self._jwks: dict[str, Any] | None = None
        self._jwks_until = 0.0

    async def close(self) -> None:
        await self.http.aclose()

    @property
    def redirect_uri(self) -> str:
        return str(self.settings.public_base_url).rstrip("/") + "/api/v1/auth/callback"

    def _key(self, kind: str, value: str) -> str:
        digest = hmac.new(_secret(self.settings.session_secret).encode(), value.encode(), hashlib.sha256).hexdigest()
        return f"itops:identity:{kind}:{digest}"

    async def metadata(self) -> dict[str, Any]:
        if self._metadata is not None and self._metadata_until > time.monotonic():
            return self._metadata
        issuer = str(self.settings.oidc_issuer).rstrip("/")
        if not issuer:
            raise HTTPException(503, "identity_unavailable")
        try:
            response = await self.http.get(issuer + "/.well-known/openid-configuration")
            response.raise_for_status()
            metadata = response.json()
            if metadata.get("issuer") != issuer:
                raise ValueError("issuer mismatch")
            for name in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
                endpoint = urlsplit(metadata[name])
                if endpoint.scheme not in {"https", "http"} or not endpoint.netloc:
                    raise ValueError("invalid endpoint")
                if urlsplit(issuer).scheme == "https" and endpoint.scheme != "https":
                    raise ValueError("insecure endpoint")
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
            raise HTTPException(503, "identity_unavailable") from error
        self._metadata, self._metadata_until = metadata, time.monotonic() + 300
        return metadata

    async def begin_login(self, return_to: str = "/") -> LoginFlow:
        if (not return_to.startswith("/") or return_to.startswith("//") or "\\" in return_to
                or any(ord(char) < 32 for char in return_to) or len(return_to) > 2000):
            raise HTTPException(400, "invalid_return_path")
        metadata = await self.metadata()
        state, verifier, nonce, browser = (secrets.token_urlsafe(32) for _ in range(4))
        flow = {"verifier": verifier, "nonce": nonce, "browser": hashlib.sha256(browser.encode()).hexdigest(), "return_to": return_to}
        await self.redis.set(self._key("state", state), json.dumps(flow), ex=self.settings.oidc_state_ttl_seconds)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        query = urlencode({"client_id": self.settings.oidc_client_id, "redirect_uri": self.redirect_uri,
                           "response_type": "code", "scope": self.settings.oidc_scopes, "state": state,
                           "nonce": nonce, "code_challenge": challenge, "code_challenge_method": "S256"})
        return LoginFlow(metadata["authorization_endpoint"] + "?" + query, browser)

    async def consume_state(self, state: str, browser_token: str) -> dict[str, Any]:
        if not state or len(state) > 256 or not browser_token or len(browser_token) > 256:
            raise HTTPException(400, "invalid_login_state")
        raw = await self.redis.getdel(self._key("state", state))
        if raw is None:
            raise HTTPException(400, "invalid_login_state")
        try:
            flow = json.loads(_text(raw))
            if not hmac.compare_digest(flow["browser"], hashlib.sha256(browser_token.encode()).hexdigest()):
                raise ValueError("browser mismatch")
        except (ValueError, TypeError, KeyError) as error:
            raise HTTPException(400, "invalid_login_state") from error
        return flow

    async def verify_id_token(self, token: str, nonce: str) -> dict[str, Any]:
        try:
            header = jwt.get_unverified_header(token)
            # Do not accept the token's algorithm as authority, or symmetric keys from JWKS.
            algorithm = header.get("alg")
            if algorithm not in {"RS256", "RS384", "RS512", "ES256", "ES384", "ES512"}:
                raise ValueError("unsupported algorithm")
            metadata = await self.metadata()
            if self._jwks is None or self._jwks_until <= time.monotonic():
                await self._refresh_jwks(metadata)
            keys = [key for key in self._jwks.get("keys", []) if key.get("kid") == header.get("kid") and key.get("use", "sig") == "sig" and key.get("alg", algorithm) == algorithm]
            if not keys:
                await self._refresh_jwks(metadata)
                keys = [key for key in self._jwks.get("keys", []) if key.get("kid") == header.get("kid") and key.get("use", "sig") == "sig" and key.get("alg", algorithm) == algorithm]
            if len(keys) != 1:
                raise ValueError("unknown signing key")
            key = jwt.PyJWK.from_dict(keys[0], algorithm=algorithm).key
            claims = jwt.decode(token, key, algorithms=[algorithm], audience=self.settings.oidc_client_id,
                                issuer=str(self.settings.oidc_issuer).rstrip("/"), leeway=self.settings.oidc_clock_skew_seconds,
                                options={"require": ["exp", "iat", "iss", "aud", "sub", "nonce"]})
            if not hmac.compare_digest(str(claims["nonce"]), nonce):
                raise ValueError("nonce mismatch")
            if not isinstance(claims["sub"], str) or not claims["sub"] or len(claims["sub"]) > 255:
                raise ValueError("invalid subject")
            audience = claims.get("aud")
            if (isinstance(audience, list) and len(audience) > 1) or "azp" in claims:
                if claims.get("azp") != self.settings.oidc_client_id:
                    raise ValueError("authorized party mismatch")
        except (jwt.PyJWTError, ValueError, KeyError, TypeError, httpx.HTTPError) as error:
            raise HTTPException(401, "invalid_identity_token") from error
        return claims

    async def _refresh_jwks(self, metadata: dict[str, Any]) -> None:
        response = await self.http.get(metadata["jwks_uri"])
        response.raise_for_status()
        value = response.json()
        if not isinstance(value.get("keys"), list):
            raise ValueError("invalid JWKS")
        self._jwks, self._jwks_until = value, time.monotonic() + 300

    def role_from_claims(self, claims: dict[str, Any]) -> str:
        roles: Any = claims
        for component in self.settings.oidc_roles_claim.split("."):
            roles = roles.get(component, {}) if isinstance(roles, dict) else {}
        if isinstance(roles, str):
            roles = [roles]
        allowed = set(self.settings.oidc_allowed_roles)
        for role in ("admin", "support", "employee"):
            if isinstance(roles, list) and role in roles and role in allowed:
                return role
        raise HTTPException(403, "enterprise_access_required")

    async def complete_login(self, code: str, state: str, browser_token: str) -> tuple[str, str]:
        flow = await self.consume_state(state, browser_token)
        if not code or len(code) > 4000:
            raise HTTPException(400, "invalid_authorization_code")
        metadata = await self.metadata()
        data = {"grant_type": "authorization_code", "code": code, "client_id": self.settings.oidc_client_id,
                "redirect_uri": self.redirect_uri, "code_verifier": flow["verifier"]}
        client_secret = _secret(self.settings.oidc_client_secret)
        if client_secret:
            data["client_secret"] = client_secret
        try:
            response = await self.http.post(metadata["token_endpoint"], data=data)
            response.raise_for_status()
            token = response.json()["id_token"]
        except (httpx.HTTPError, ValueError, KeyError) as error:
            raise HTTPException(401, "identity_exchange_failed") from error
        claims = await self.verify_id_token(token, flow["nonce"])
        role = self.role_from_claims(claims)
        user_id = hashlib.sha256((claims["iss"] + "\x00" + claims["sub"]).encode()).hexdigest()
        display_name = str(claims.get("name") or claims.get("preferred_username") or "企业用户")[:200]
        async with self.session_factory.begin() as session:
            # ON CONFLICT makes simultaneous first logins for the same subject safe.
            await session.execute(insert(User).values(id=user_id, display_name=display_name, access_level=role)
                                  .on_conflict_do_nothing(index_elements=["id"]))
            await session.execute(insert(IdentityAccount).values(user_id=user_id, issuer=claims["iss"], subject=claims["sub"])
                                  .on_conflict_do_nothing(index_elements=["issuer", "subject"]))
            account = await session.scalar(select(IdentityAccount).where(IdentityAccount.user_id == user_id).with_for_update())
            if account is None or not account.enabled:
                raise HTTPException(403, "account_disabled")
            user = await session.get(User, user_id)
            effective_role = account.role_override or role
            previous_role = user.access_level
            user.display_name, user.access_level = display_name, effective_role
            if account.role_override is None and previous_role != effective_role:
                account.version += 1
                account.updated_at = datetime.now(timezone.utc)
            if {"employee": 0, "support": 1, "admin": 2}.get(effective_role, -1) < {"employee": 0, "support": 1, "admin": 2}.get(previous_role, -1):
                await self._invalidate_runs(session, user_id)
            account.last_login_at = datetime.now(timezone.utc)
        session_id = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(32)
        await self.redis.set(self._key("session", session_id), json.dumps({"user_id": user_id, "csrf_token": csrf}), ex=self.settings.session_ttl_seconds)
        return session_id, flow["return_to"]

    async def principal(self, session_id: str) -> Principal:
        if not session_id or len(session_id) > 256:
            raise _unauthorized()
        raw = await self.redis.get(self._key("session", session_id))
        if raw is None:
            raise _unauthorized()
        try:
            value = json.loads(_text(raw))
            async with self.session_factory() as session:
                row = (await session.execute(select(User, IdentityAccount).join(IdentityAccount, IdentityAccount.user_id == User.id)
                                             .where(User.id == value["user_id"], IdentityAccount.enabled.is_(True)))).first()
            if row is None or row[0].access_level not in self.settings.oidc_allowed_roles:
                raise _unauthorized()
            user = row[0]
            return Principal(user.id, user.display_name, user.access_level, session_id, value["csrf_token"])
        except (ValueError, KeyError, TypeError) as error:
            raise _unauthorized() from error

    async def logout(self, session_id: str) -> None:
        await self.redis.delete(self._key("session", session_id))

    @staticmethod
    def account_payload(user: User, account: IdentityAccount) -> dict[str, Any]:
        return {"id": user.id, "display_name": user.display_name, "role": user.access_level,
                "enabled": account.enabled, "version": account.version}

    @staticmethod
    async def _admin_actor(session, principal: Principal) -> None:
        authorized = await session.scalar(select(User.id).join(IdentityAccount, IdentityAccount.user_id == User.id)
            .where(User.id == principal.user_id, User.access_level == "admin", IdentityAccount.enabled.is_(True)))
        if authorized is None:
            raise HTTPException(403, "admin_access_required")

    async def accounts(self, principal: Principal, cursor: str | None = None, limit: int = 50) -> dict[str, Any]:
        limit = max(1, min(limit, 100))
        async with self.session_factory() as session:
            await self._admin_actor(session, principal)
            query = select(User, IdentityAccount).join(IdentityAccount, IdentityAccount.user_id == User.id)
            if cursor:
                query = query.where(User.id > cursor)
            rows = list((await session.execute(query.order_by(User.id).limit(limit + 1))).all())
            return {"items": [self.account_payload(user, account) for user, account in rows[:limit]],
                    "next_cursor": rows[limit - 1][0].id if len(rows) > limit else None}

    async def _invalidate_runs(self, session, user_id: str) -> None:
        from app.production.run_models import ProductionRun
        from app.production.runs import RunService
        runs = list(await session.scalars(select(ProductionRun).where(ProductionRun.user_id == user_id,
            ProductionRun.status.in_({"queued", "running"})).with_for_update()))
        service = RunService(self.settings, self.session_factory, self.redis)
        for run in runs:
            await service._finish(session, run, "failed", {"answer": "账号访问权限已变更，运行已停止。",
                "final_state": "handoff", "error": "account_access_changed"})

    async def update_account(self, principal: Principal, user_id: str, *, expected_version: int,
                             role: str | None = None, enabled: bool | None = None) -> dict[str, Any]:
        if role is not None and role not in {"employee", "support", "admin"}:
            raise HTTPException(422, "invalid_account_role")
        async with self.session_factory.begin() as session:
            # One enterprise: serialize admin membership changes so concurrent edits
            # cannot each observe the other administrator and remove both.
            await session.execute(text("SELECT pg_advisory_xact_lock(782634903)"))
            await self._admin_actor(session, principal)
            account = await session.scalar(select(IdentityAccount).where(IdentityAccount.user_id == user_id).with_for_update())
            if account is None:
                raise HTTPException(404, "account_not_found")
            # Roles are non-key attributes. FOR NO KEY UPDATE permits worker audit/
            # escalation foreign-key checks while this transaction waits for a run.
            user = await session.scalar(select(User).where(User.id == user_id).with_for_update(key_share=True))
            if account.version != expected_version:
                raise HTTPException(409, "account_version_conflict")
            next_role = role if role is not None else user.access_level
            next_enabled = enabled if enabled is not None else account.enabled
            if account.enabled and user.access_level == "admin" and (not next_enabled or next_role != "admin"):
                admins = await session.scalar(select(func.count()).select_from(User).join(IdentityAccount, IdentityAccount.user_id == User.id)
                    .where(User.access_level == "admin", IdentityAccount.enabled.is_(True)))
                if admins <= 1:
                    raise HTTPException(409, "last_enabled_admin")
            previous_role, previous_enabled = user.access_level, account.enabled
            changes: dict[str, Any] = {}
            if role is not None:
                changes["role"] = {"from": previous_role, "to": next_role}
                account.role_override = role
                user.access_level = role
            if enabled is not None:
                changes["enabled"] = {"from": previous_enabled, "to": next_enabled}
                account.enabled = enabled
            account.version += 1
            account.updated_at = datetime.now(timezone.utc)
            from app.production.business_models import BusinessAudit
            session.add(BusinessAudit(entity_type="identity_account", entity_id=account.id, actor_id=principal.user_id,
                event_type="updated", details={"version": account.version, "changes": changes}))
            if not next_enabled or {"employee": 0, "support": 1, "admin": 2}[next_role] < {"employee": 0, "support": 1, "admin": 2}[previous_role]:
                await self._invalidate_runs(session, user_id)
            return self.account_payload(user, account)


def validate_csrf(request: Request, principal: Principal, settings) -> None:
    expected = urlsplit(str(settings.public_base_url))
    allowed_origin = f"{expected.scheme}://{expected.netloc}"
    origin = request.headers.get("origin", "")
    token = request.headers.get("x-csrf-token", "")
    if origin != allowed_origin or not token or not hmac.compare_digest(token, principal.csrf_token):
        raise HTTPException(403, "csrf_validation_failed")


async def require_principal(request: Request) -> Principal:
    service: IdentityService = request.app.state.identity_service
    principal = await service.principal(request.cookies.get(service.settings.session_cookie_name, ""))
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        validate_csrf(request, principal, service.settings)
    return principal


async def require_support(principal: Annotated[Principal, Depends(require_principal)]) -> Principal:
    if principal.role not in {"support", "admin"}:
        raise HTTPException(403, "support_access_required")
    return principal


async def require_admin(principal: Annotated[Principal, Depends(require_principal)]) -> Principal:
    if principal.role != "admin":
        raise HTTPException(403, "admin_access_required")
    return principal
