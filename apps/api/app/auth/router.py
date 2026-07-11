"""Routes d'authentification (Phase 2 T4-T6).

Ces routes (+ health) sont LES seules routes anonymes de l'API (invariant n°1) :
login (+ second facteur), OAuth start/callback, acceptation d'invitation.
Tout le reste passe par `require_permission`.
"""

import uuid
from typing import Annotated, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import service
from app.auth.deps import CurrentAuth, current_auth
from app.auth.models import OAuthProvider
from app.auth.oauth import OAuthLoginError, build_authorization_url, complete_login, parse_state
from app.auth.rate_limit import enforce_rate_limit
from app.core.config import Settings, get_settings
from app.core.db import get_control_session
from app.directory.models import Membership
from app.directory.service import DirectoryError, accept_invitation
from app.tenancy.models import Tenant

router = APIRouter(prefix="/auth", tags=["auth"])

ControlSession = Annotated[AsyncSession, Depends(get_control_session)]


def set_session_cookie(response: Response, token: str, settings: Settings) -> None:
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        max_age=settings.session_ttl_hours * 3600,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        domain=settings.session_cookie_domain or None,
        path="/",
    )


def clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        key=settings.session_cookie_name,
        domain=settings.session_cookie_domain or None,
        path="/",
    )


# --- Login mot de passe (+ second facteur TOTP) ---


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1)


class LoginResponse(BaseModel):
    status: Literal["ok", "totp_required"]
    challenge_token: str | None = None


@router.post("/login", operation_id="login")
async def login(
    payload: LoginRequest, request: Request, response: Response, session: ControlSession
) -> LoginResponse:
    await enforce_rate_limit(request, "login", payload.email)
    result = await service.authenticate(session, payload.email, payload.password)
    if result is None:
        # Réponse indistincte : jamais d'oracle email inconnu / mauvais mot de passe.
        raise HTTPException(status_code=401, detail="Identifiants invalides")
    user, credentials = result
    if credentials.totp_enabled:
        challenge_token = await service.create_login_challenge(session, user.id)
        await session.commit()
        return LoginResponse(status="totp_required", challenge_token=challenge_token)
    token = await service.create_session(session, user.id)
    await session.commit()
    set_session_cookie(response, token, get_settings())
    return LoginResponse(status="ok")


class TotpLoginRequest(BaseModel):
    challenge_token: str
    code: str = Field(min_length=6, max_length=16)


@router.post("/login/totp", operation_id="loginTotp")
async def login_totp(
    payload: TotpLoginRequest, request: Request, response: Response, session: ControlSession
) -> LoginResponse:
    await enforce_rate_limit(request, "login-totp", payload.challenge_token)
    user_id = await service.consume_login_challenge(session, payload.challenge_token)
    if user_id is None or not await service.verify_second_factor(session, user_id, payload.code):
        await session.commit()  # le challenge consommé reste consommé
        raise HTTPException(status_code=401, detail="Code invalide ou challenge expiré")
    token = await service.create_session(session, user_id)
    await session.commit()
    set_session_cookie(response, token, get_settings())
    return LoginResponse(status="ok")


class StatusResponse(BaseModel):
    status: Literal["ok"] = "ok"


@router.post("/logout", operation_id="logout")
async def logout(
    response: Response, auth: Annotated[CurrentAuth, Depends(current_auth)], session: ControlSession
) -> StatusResponse:
    await service.revoke_session(session, auth.session)
    session.add(auth.session)
    await session.commit()
    clear_session_cookie(response, get_settings())
    return StatusResponse()


# --- Profil courant ---


class MembershipInfo(BaseModel):
    tenant_slug: str
    role: str


class MeResponse(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str | None
    totp_enabled: bool
    memberships: list[MembershipInfo]


@router.get("/me", operation_id="getMe")
async def get_me(
    auth: Annotated[CurrentAuth, Depends(current_auth)], session: ControlSession
) -> MeResponse:
    credentials = await service.get_credentials(session, auth.user.id)
    rows = await session.execute(
        select(Tenant.slug, Membership.role)
        .join(Membership, Membership.tenant_id == Tenant.id)
        .where(Membership.user_id == auth.user.id)
        .order_by(Tenant.slug)
    )
    return MeResponse(
        id=auth.user.id,
        email=auth.user.email,
        display_name=auth.user.display_name,
        totp_enabled=credentials.totp_enabled if credentials else False,
        memberships=[MembershipInfo(tenant_slug=slug, role=role) for slug, role in rows.all()],
    )


# --- TOTP : enrôlement, activation, désactivation ---


class TotpSetupResponse(BaseModel):
    secret: str
    otpauth_uri: str


@router.post("/totp/setup", operation_id="setupTotp")
async def totp_setup(
    auth: Annotated[CurrentAuth, Depends(current_auth)], session: ControlSession
) -> TotpSetupResponse:
    try:
        secret, uri = await service.setup_totp(session, auth.user)
    except service.AuthError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await session.commit()
    return TotpSetupResponse(secret=secret, otpauth_uri=uri)


class TotpActivateRequest(BaseModel):
    code: str = Field(min_length=6, max_length=8)


class TotpActivateResponse(BaseModel):
    recovery_codes: list[str]


@router.post("/totp/activate", operation_id="activateTotp")
async def totp_activate(
    payload: TotpActivateRequest,
    auth: Annotated[CurrentAuth, Depends(current_auth)],
    session: ControlSession,
) -> TotpActivateResponse:
    try:
        codes = await service.activate_totp(session, auth.user.id, payload.code)
    except service.AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await session.commit()
    return TotpActivateResponse(recovery_codes=codes)


class TotpDisableRequest(BaseModel):
    password: str = Field(min_length=1)


@router.post("/totp/disable", operation_id="disableTotp")
async def totp_disable(
    payload: TotpDisableRequest,
    auth: Annotated[CurrentAuth, Depends(current_auth)],
    session: ControlSession,
) -> StatusResponse:
    try:
        await service.disable_totp(session, auth.user.id, payload.password)
    except service.AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await session.commit()
    return StatusResponse()


# --- Acceptation d'invitation (seule porte d'entrée, invariant n°5) ---


class AcceptInvitationRequest(BaseModel):
    token: str
    password: str | None = Field(default=None, min_length=12, max_length=256)
    display_name: str | None = Field(default=None, max_length=255)


@router.post("/invitations/accept", operation_id="acceptInvitation")
async def invitations_accept(
    payload: AcceptInvitationRequest, request: Request, session: ControlSession
) -> StatusResponse:
    await enforce_rate_limit(request, "invitation-accept", payload.token)
    try:
        await accept_invitation(
            session,
            payload.token,
            password=payload.password,
            display_name=payload.display_name,
        )
    except DirectoryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await session.commit()
    return StatusResponse()


# --- OAuth login Google / Microsoft (décision D5 : invitation only) ---


def _apex_host() -> str:
    return urlsplit(get_settings().public_base_url).netloc


def _validate_return_host(host: str) -> str:
    """Le retour post-OAuth ne peut viser que l'apex ou un de ses sous-domaines."""
    apex = _apex_host()
    bare = host.split(":", 1)[0]
    apex_bare = apex.split(":", 1)[0]
    if bare == apex_bare or bare.endswith("." + apex_bare):
        return host
    return apex


@router.get("/oauth/{provider}/start", operation_id="oauthStart")
async def oauth_start(provider: OAuthProvider, request: Request) -> RedirectResponse:
    return_host = _validate_return_host(request.headers.get("host", _apex_host()))
    try:
        url = await build_authorization_url(provider, return_host)
    except OAuthLoginError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return RedirectResponse(url, status_code=307)


@router.get("/oauth/{provider}/callback", operation_id="oauthCallback")
async def oauth_callback(
    provider: OAuthProvider, code: str, state: str, session: ControlSession
) -> RedirectResponse:
    try:
        return_host, nonce = parse_state(state, provider)
        user = await complete_login(session, provider, code, nonce)
    except OAuthLoginError as exc:
        # Message générique : ne révèle ni l'existence des comptes ni la cause précise.
        raise HTTPException(status_code=403, detail="Connexion OAuth refusée") from exc
    token = await service.create_session(session, user.id)
    await session.commit()

    settings = get_settings()
    scheme = urlsplit(settings.public_base_url).scheme
    response = RedirectResponse(
        f"{scheme}://{_validate_return_host(return_host)}/", status_code=303
    )
    set_session_cookie(response, token, settings)
    return response
