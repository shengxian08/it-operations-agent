import base64
import hashlib
import json
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from starlette.requests import Request


class MemoryRedis:
    def __init__(self):
        self.values = {}

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def get(self, key):
        return self.values.get(key)

    async def getdel(self, key):
        return self.values.pop(key, None)

    async def delete(self, key):
        return self.values.pop(key, None) is not None


def settings():
    return SimpleNamespace(
        oidc_issuer="https://identity.example/realms/company",
        oidc_client_id="assistant", oidc_client_secret=None,
        oidc_roles_claim="realm_access.roles",
        oidc_allowed_roles=("employee", "support", "admin"),
        oidc_scopes="openid profile email", oidc_clock_skew_seconds=0,
        oidc_http_timeout_seconds=5, oidc_state_ttl_seconds=300,
        public_base_url="https://assistant.example", session_secret="x" * 40,
        session_ttl_seconds=3600, session_cookie_name="it_assistant_session",
        cookie_secure=True,
    )


def identity_module():
    import importlib.util
    try:
        available = importlib.util.find_spec("app.production.identity")
    except ModuleNotFoundError:
        available = None
    assert available, "production identity service is missing"
    from app.production import identity
    return identity


def oidc_provider():
    config = settings()
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update(kid="company-key", alg="RS256", use="sig")
    metadata = {
        "issuer": config.oidc_issuer,
        "authorization_endpoint": config.oidc_issuer + "/authorize",
        "token_endpoint": config.oidc_issuer + "/token",
        "jwks_uri": config.oidc_issuer + "/certs",
    }
    def handle(request):
        if request.url.path.endswith("openid-configuration"):
            return httpx.Response(200, json=metadata)
        if request.url.path.endswith("certs"):
            return httpx.Response(200, json={"keys": [jwk]})
        raise AssertionError(request.url)
    return config, key, httpx.AsyncClient(transport=httpx.MockTransport(handle))


@pytest.mark.asyncio
async def test_login_state_has_pkce_nonce_and_is_bound_to_browser():
    module = identity_module()
    config, key, client = oidc_provider()
    service = module.IdentityService(config, MemoryRedis(), None)
    service.http = client
    login = await service.begin_login("/conversations")
    query = parse_qs(urlparse(login.url).query)
    assert query["code_challenge_method"] == ["S256"]
    flow = await service.consume_state(query["state"][0], login.browser_token)
    expected = base64.urlsafe_b64encode(hashlib.sha256(flow["verifier"].encode()).digest()).rstrip(b"=").decode()
    assert query["code_challenge"] == [expected]
    assert query["nonce"] == [flow["nonce"]]
    assert flow["return_to"] == "/conversations"
    with pytest.raises(HTTPException) as error:
        await service.consume_state(query["state"][0], login.browser_token)
    assert error.value.status_code == 400
    await client.aclose()


@pytest.mark.asyncio
async def test_login_state_rejects_cross_browser_and_external_return_paths():
    module = identity_module()
    config, key, client = oidc_provider()
    service = module.IdentityService(config, MemoryRedis(), None)
    service.http = client
    for path in ["https://attacker.example", "//attacker.example", "/\\attacker.example"]:
        with pytest.raises(HTTPException):
            await service.begin_login(path)
    login = await service.begin_login("/")
    state = parse_qs(urlparse(login.url).query)["state"][0]
    with pytest.raises(HTTPException):
        await service.consume_state(state, "other-browser")
    await client.aclose()


@pytest.mark.asyncio
async def test_id_token_checks_signature_issuer_audience_nonce_and_enterprise_roles():
    module = identity_module()
    config, key, client = oidc_provider()
    service = module.IdentityService(config, MemoryRedis(), None)
    service.http = client
    claims = {"iss": config.oidc_issuer, "aud": config.oidc_client_id, "sub": "employee-1",
              "exp": int(time.time()) + 300, "iat": int(time.time()), "nonce": "expected",
              "realm_access": {"roles": ["employee", "support"]}, "name": "员工"}
    token = jwt.encode(claims, key, algorithm="RS256", headers={"kid": "company-key"})
    verified = await service.verify_id_token(token, "expected")
    assert verified["sub"] == "employee-1"
    assert service.role_from_claims(verified) == "support"
    for changes in [{"nonce": "bad"}, {"aud": "other"}, {"iss": "https://other"}, {"exp": 1}]:
        bad = jwt.encode(claims | changes, key, algorithm="RS256", headers={"kid": "company-key"})
        with pytest.raises(HTTPException):
            await service.verify_id_token(bad, "expected")
    with pytest.raises(HTTPException):
        service.role_from_claims(claims | {"realm_access": {"roles": ["unknown"]}})
    await client.aclose()


def test_csrf_requires_exact_origin_and_session_bound_token():
    module = identity_module()
    principal = module.Principal("user", "员工", "employee", "session", "csrf-value")
    for origin, token in [("https://attacker.example", "csrf-value"), ("https://assistant.example", "wrong"), (None, "csrf-value")]:
        headers = [(b"x-csrf-token", token.encode())]
        if origin:
            headers.append((b"origin", origin.encode()))
        request = Request({"type": "http", "method": "POST", "headers": headers})
        with pytest.raises(HTTPException) as error:
            module.validate_csrf(request, principal, settings())
        assert error.value.status_code == 403
    request = Request({"type": "http", "method": "POST", "headers": [(b"origin", b"https://assistant.example"), (b"x-csrf-token", b"csrf-value")]})
    module.validate_csrf(request, principal, settings())


def test_principal_repr_does_not_expose_session_or_csrf_credentials():
    module = identity_module()
    principal = module.Principal("user", "员工", "employee", "session-private", "csrf-private")
    assert "session-private" not in repr(principal)
    assert "csrf-private" not in repr(principal)
