from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

from app.production.identity import Principal, require_admin, require_principal


router = APIRouter(prefix="/api/v1", tags=["identity"])
FLOW_COOKIE = "itops_oidc_flow"


class AccountPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)
    role: Literal["employee", "support", "admin"] | None = None
    enabled: bool | None = None


@router.get("/admin/accounts")
async def list_accounts(request: Request, principal: Annotated[Principal, Depends(require_admin)],
                        cursor: str | None = Query(None, max_length=128), limit: int = Query(50, ge=1, le=100)):
    return await request.app.state.identity_service.accounts(principal, cursor, limit)


@router.patch("/admin/accounts/{user_id}")
async def patch_account(request: Request, user_id: str, body: AccountPatch,
                        principal: Annotated[Principal, Depends(require_admin)]):
    return await request.app.state.identity_service.update_account(principal, user_id,
        expected_version=body.expected_version, role=body.role, enabled=body.enabled)


@router.get("/me")
async def me(principal: Annotated[Principal, Depends(require_principal)]):
    return {"id": principal.user_id, "display_name": principal.display_name,
            "role": principal.role, "csrf_token": principal.csrf_token}


@router.get("/auth/login")
async def login(request: Request, return_to: str = Query("/", max_length=2000)):
    service = request.app.state.identity_service
    flow = await service.begin_login(return_to)
    response = RedirectResponse(flow.url, status_code=302)
    response.set_cookie(FLOW_COOKIE, flow.browser_token, max_age=service.settings.oidc_state_ttl_seconds,
                        secure=service.settings.cookie_secure, httponly=True, samesite="lax", path="/api/v1/auth")
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/auth/callback")
async def callback(request: Request, code: str = "", state: str = "", error: str | None = None):
    if error:
        raise HTTPException(401, "identity_login_failed")
    service = request.app.state.identity_service
    session_id, return_to = await service.complete_login(code, state, request.cookies.get(FLOW_COOKIE, ""))
    previous = request.cookies.get(service.settings.session_cookie_name)
    if previous:
        await service.logout(previous)
    response = RedirectResponse(return_to, status_code=302)
    response.set_cookie(service.settings.session_cookie_name, session_id, max_age=service.settings.session_ttl_seconds,
                        secure=service.settings.cookie_secure, httponly=True, samesite="lax", path="/")
    response.delete_cookie(FLOW_COOKIE, path="/api/v1/auth", secure=service.settings.cookie_secure, httponly=True, samesite="lax")
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/auth/logout", status_code=204)
async def logout(request: Request, principal: Annotated[Principal, Depends(require_principal)]):
    service = request.app.state.identity_service
    await service.logout(principal.session_id)
    response = Response(status_code=204)
    response.delete_cookie(service.settings.session_cookie_name, path="/", secure=service.settings.cookie_secure, httponly=True, samesite="lax")
    response.headers["Cache-Control"] = "no-store"
    return response
