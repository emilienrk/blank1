"""Flux OAuth tiers des connecteurs (Phase 5 T3) — distinct du login.

Même mécanique de state signé HMAC auto-porteur que la Phase 2, mais apps OAuth
dédiées (décision D3) et scopes d'API. Le state transporte tenant, user, kind et
capabilities demandées : le callback (route anonyme sur l'apex — liste fermée,
invariant n°9) repose le contexte tenant depuis le state pour écrire la
connexion en DB tenant, crée la route de webhook control-plane (D6) et audite
`connector.connected`.
"""

import uuid
from datetime import UTC, datetime
from typing import Annotated
from urllib.parse import urlsplit

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import record_audit_event
from app.auth.tokens import InvalidSignedPayloadError, generate_token, sign_payload, verify_payload
from app.connectors import service
from app.connectors.models import WebhookRoute
from app.connectors.registry import (
    KNOWN_CAPABILITIES,
    ProviderManifest,
    get_provider,
)
from app.connectors.tenant_models import (
    ConnectionKind,
    ConnectionStatus,
    ConnectorConnection,
    ConnectorProvider,
)
from app.core.config import Settings, get_settings
from app.core.db import get_control_session
from app.tenancy.context import TenantContext, tenant_context
from app.tenancy.models import Tenant, TenantState
from app.tenancy.session import tenant_session

logger = structlog.get_logger()

STATE_TTL_SECONDS = 600

router = APIRouter(prefix="/connectors", tags=["connectors"])

ControlSession = Annotated[AsyncSession, Depends(get_control_session)]


class ConnectorOAuthError(RuntimeError):
    """Échec du flux OAuth connecteur (state, échange de code, tenant)."""


def redirect_uri(provider: ConnectorProvider, settings: Settings) -> str:
    return f"{settings.public_base_url}/api/v1/connectors/{provider.value}/callback"


def _apex_host(settings: Settings) -> str:
    return urlsplit(settings.public_base_url).netloc


def _validate_return_host(host: str, settings: Settings) -> str:
    """Le retour post-OAuth ne peut viser que l'apex ou un de ses sous-domaines."""
    apex = _apex_host(settings)
    bare = host.split(":", 1)[0]
    apex_bare = apex.split(":", 1)[0]
    if bare == apex_bare or bare.endswith("." + apex_bare):
        return host
    return apex


def validate_capabilities(manifest: ProviderManifest, capabilities: list[str]) -> list[str]:
    unknown = [c for c in capabilities if c not in KNOWN_CAPABILITIES]
    if unknown or not capabilities:
        msg = f"Capabilities invalides : {unknown or 'aucune'}"
        raise ConnectorOAuthError(msg)
    unsupported = [c for c in capabilities if c not in manifest.capabilities]
    if unsupported:
        msg = f"Capabilities non supportées par {manifest.provider.value} : {unsupported}"
        raise ConnectorOAuthError(msg)
    return capabilities


def build_authorization_url(
    provider: ConnectorProvider,
    *,
    tenant_slug: str,
    user_id: uuid.UUID,
    kind: ConnectionKind,
    capabilities: list[str],
    return_host: str,
    connection_id: uuid.UUID | None = None,
) -> str:
    """URL d'autorisation provider ; le state signé porte tout le contexte."""
    settings = get_settings()
    manifest = get_provider(provider)
    validate_capabilities(manifest, capabilities)
    # Vérifie que l'app OAuth connecteurs est configurée avant de rediriger.
    client_id, _ = service.client_credentials(manifest, settings)
    state = sign_payload(
        {
            "p": provider.value,
            "t": tenant_slug,
            "u": str(user_id),
            "k": kind.value,
            "c": capabilities,
            "r": _validate_return_host(return_host, settings),
            "cid": str(connection_id) if connection_id is not None else None,
        },
        ttl_seconds=STATE_TTL_SECONDS,
    )
    params = httpx.QueryParams(
        response_type="code",
        client_id=client_id,
        redirect_uri=redirect_uri(provider, settings),
        scope=" ".join(manifest.scopes_for(capabilities)),
        state=state,
        **dict(manifest.authorization_extra_params),
    )
    return f"{manifest.authorization_endpoint}?{params}"


def parse_state(state: str, expected_provider: ConnectorProvider) -> dict[str, object]:
    try:
        payload = verify_payload(state)
    except InvalidSignedPayloadError as exc:
        msg = "State OAuth invalide ou expiré."
        raise ConnectorOAuthError(msg) from exc
    if payload.get("p") != expected_provider.value:
        msg = "State OAuth émis pour un autre provider."
        raise ConnectorOAuthError(msg)
    if not isinstance(payload.get("t"), str) or not isinstance(payload.get("c"), list):
        msg = "State OAuth incomplet."
        raise ConnectorOAuthError(msg)
    return payload


async def _upsert_connection(
    session: AsyncSession,
    *,
    provider: ConnectorProvider,
    kind: ConnectionKind,
    user_id: uuid.UUID | None,
    account_label: str,
    scopes: list[str],
    bundle: service.TokenBundle,
    connection_id: uuid.UUID | None,
) -> ConnectorConnection:
    connection: ConnectorConnection | None = None
    if connection_id is not None:
        # Re-consentement : on ranime la connexion existante.
        connection = await session.get(ConnectorConnection, connection_id)
    if connection is None:
        connection = await session.scalar(
            select(ConnectorConnection).where(
                ConnectorConnection.provider == provider,
                ConnectorConnection.kind == kind,
                ConnectorConnection.account_label == account_label,
                ConnectorConnection.status != ConnectionStatus.REVOKED,
            )
        )
    if connection is None:
        if bundle.refresh_token is None:
            msg = (
                f"{provider.value} n'a pas émis de refresh token — reconnectez le compte "
                "(chez Google, le consentement doit être re-demandé avec prompt=consent)."
            )
            raise service.MissingRefreshTokenError(msg)
        connection = ConnectorConnection(
            provider=provider,
            kind=kind,
            user_id=user_id,
            account_label=account_label,
            scopes=scopes,
            access_token_enc=b"",
            refresh_token_enc=b"",
            access_token_expires_at=datetime.now(UTC),
        )
        session.add(connection)
    else:
        connection.account_label = account_label
        # Union des scopes : un re-consentement n'en retire jamais.
        connection.scopes = sorted(set(connection.scopes) | set(scopes))
    service.apply_token_bundle(connection, bundle)
    if not connection.refresh_token_enc:
        msg = f"{provider.value} n'a pas émis de refresh token pour cette connexion."
        raise service.MissingRefreshTokenError(msg)
    await session.flush()
    return connection


async def _ensure_webhook_route(
    control_session: AsyncSession,
    *,
    tenant: Tenant,
    provider: ConnectorProvider,
    connection_id: uuid.UUID,
) -> WebhookRoute:
    route = await control_session.scalar(
        select(WebhookRoute).where(WebhookRoute.connection_id == connection_id)
    )
    if route is None:
        route = WebhookRoute(
            route_key=generate_token(),
            provider=provider.value,
            tenant_id=tenant.id,
            connection_id=connection_id,
        )
        control_session.add(route)
        await control_session.flush()
    return route


@router.get("/{provider}/callback", operation_id="connectorOauthCallback")
async def connector_oauth_callback(
    provider: ConnectorProvider,
    state: str,
    control_session: ControlSession,
    code: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    """Route ANONYME sur l'apex (liste fermée, invariant n°9) : le state signé
    est la seule autorité — il repose le contexte tenant."""
    settings = get_settings()
    try:
        payload = parse_state(state, provider)
    except ConnectorOAuthError as exc:
        raise HTTPException(status_code=400, detail="State OAuth invalide") from exc

    return_host = _validate_return_host(str(payload.get("r", "")), settings)
    scheme = urlsplit(settings.public_base_url).scheme
    connectors_page = f"{scheme}://{return_host}/connectors"

    if error is not None or code is None:
        # Consentement refusé chez le provider : retour SPA, aucun état créé.
        return RedirectResponse(f"{connectors_page}?error=denied", status_code=303)

    tenant = await control_session.scalar(select(Tenant).where(Tenant.slug == str(payload["t"])))
    if tenant is None or tenant.state is not TenantState.ACTIVE or tenant.deleted_at:
        raise HTTPException(status_code=400, detail="Tenant introuvable ou inactif")

    manifest = get_provider(provider)
    kind = ConnectionKind(str(payload.get("k", ConnectionKind.TENANT.value)))
    capabilities = [str(c) for c in payload["c"]]  # type: ignore[union-attr]
    user_value = payload.get("u")
    user_id = uuid.UUID(str(user_value)) if isinstance(user_value, str) else None
    cid_value = payload.get("cid")
    reconsent_id = uuid.UUID(str(cid_value)) if isinstance(cid_value, str) else None

    try:
        bundle = await service.exchange_code(manifest, code, redirect_uri(provider, settings))
        account_label = await service.fetch_account_label(manifest, bundle.access_token)
    except service.ConnectorError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Provider OAuth injoignable") from exc

    granted_scopes = bundle.scopes or manifest.scopes_for(capabilities)

    ctx = TenantContext(tenant_id=tenant.id, slug=tenant.slug)
    with tenant_context(ctx):
        async with tenant_session() as scoped_session:
            try:
                connection = await _upsert_connection(
                    scoped_session,
                    provider=provider,
                    kind=kind,
                    user_id=user_id if kind is ConnectionKind.USER else None,
                    account_label=account_label,
                    scopes=granted_scopes,
                    bundle=bundle,
                    connection_id=reconsent_id,
                )
            except service.MissingRefreshTokenError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            await record_audit_event(
                scoped_session,
                action="connector.connected",
                resource_type="connector_connection",
                resource_id=str(connection.id),
                payload={
                    "provider": provider.value,
                    "account": account_label,
                    "kind": kind.value,
                    "capabilities": capabilities,
                },
                actor_user_id=user_id,
                actor_label=account_label,
            )
            await scoped_session.commit()
            connection_id = connection.id

    await _ensure_webhook_route(
        control_session, tenant=tenant, provider=provider, connection_id=connection_id
    )
    await control_session.commit()

    # Les subscriptions webhook se créent hors requête (appels providers lourds).
    from app.connectors import tasks as connector_tasks

    await connector_tasks.enqueue_subscription_sync(tenant.slug, connection_id)

    logger.info(
        "connector_connected",
        provider=provider.value,
        connection_id=str(connection_id),
        kind=kind.value,
    )
    return RedirectResponse(f"{connectors_page}?connected={provider.value}", status_code=303)
