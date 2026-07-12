"""Routes de gestion des connecteurs (Phase 5 T3) — sous contexte tenant.

`core.connectors.read` (tous rôles) : liste, statuts, santé. `core.connectors.manage`
(owner/admin) : connexion (start OAuth), re-consentement, révocation. La réponse
ne porte JAMAIS de token — statuts et labels uniquement (invariant n°1 Phase 5).
"""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import record_audit_event
from app.auth.deps import CurrentAuth, current_auth
from app.auth.permissions import require_permission
from app.connectors import service
from app.connectors.models import WebhookRoute
from app.connectors.oauth import ConnectorOAuthError, build_authorization_url
from app.connectors.tenant_models import (
    ConnectionKind,
    ConnectionStatus,
    ConnectorConnection,
    ConnectorProvider,
    ConnectorSubscription,
)
from app.core.db import get_control_session
from app.tenancy.context import TenantContext
from app.tenancy.session import get_tenant_session

router = APIRouter(prefix="/connectors", tags=["connectors"])

ControlSession = Annotated[AsyncSession, Depends(get_control_session)]
TenantSession = Annotated[AsyncSession, Depends(get_tenant_session)]


class StatusResponse(BaseModel):
    status: Literal["ok"] = "ok"


class ConnectionOut(BaseModel):
    id: uuid.UUID
    provider: ConnectorProvider
    kind: ConnectionKind
    account_label: str
    scopes: list[str]
    status: ConnectionStatus
    last_error: str | None
    health_checked_at: datetime | None
    access_token_expires_at: datetime
    created_at: datetime


def _to_out(connection: ConnectorConnection) -> ConnectionOut:
    return ConnectionOut(
        id=connection.id,
        provider=connection.provider,
        kind=connection.kind,
        account_label=connection.account_label,
        scopes=connection.scopes,
        status=connection.status,
        last_error=connection.last_error,
        health_checked_at=connection.health_checked_at,
        access_token_expires_at=connection.access_token_expires_at,
        created_at=connection.created_at,
    )


@router.get("", operation_id="listConnectors")
async def connectors_list(
    ctx: Annotated[TenantContext, Depends(require_permission("core.connectors.read"))],
    tenant_session: TenantSession,
) -> list[ConnectionOut]:
    connections = await tenant_session.scalars(
        select(ConnectorConnection).order_by(ConnectorConnection.created_at)
    )
    return [_to_out(connection) for connection in connections.all()]


class AuthorizationUrlResponse(BaseModel):
    authorization_url: str


@router.get("/{provider}/start", operation_id="startConnector")
async def connector_start(
    provider: ConnectorProvider,
    request: Request,
    ctx: Annotated[TenantContext, Depends(require_permission("core.connectors.manage"))],
    auth: Annotated[CurrentAuth, Depends(current_auth)],
    capabilities: Annotated[list[str] | None, Query()] = None,
    kind: ConnectionKind = ConnectionKind.TENANT,
) -> AuthorizationUrlResponse:
    """Démarre le flux OAuth tiers : la SPA redirige vers l'URL retournée."""
    try:
        url = build_authorization_url(
            provider,
            tenant_slug=ctx.slug,
            user_id=auth.user.id,
            kind=kind,
            capabilities=capabilities or ["mail", "calendar"],
            return_host=request.headers.get("host", ""),
        )
    except ConnectorOAuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except service.ConnectorError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return AuthorizationUrlResponse(authorization_url=url)


@router.post("/{connection_id}/reconsent", operation_id="reconsentConnector")
async def connector_reconsent(
    connection_id: uuid.UUID,
    request: Request,
    ctx: Annotated[TenantContext, Depends(require_permission("core.connectors.manage"))],
    auth: Annotated[CurrentAuth, Depends(current_auth)],
    tenant_session: TenantSession,
) -> AuthorizationUrlResponse:
    """Relance un flux OAuth sur une connexion `needs_reconsent` (§5 : guidé)."""
    connection = await tenant_session.get(ConnectorConnection, connection_id)
    if connection is None:
        raise HTTPException(status_code=404, detail="Connexion introuvable")
    if connection.status is not ConnectionStatus.NEEDS_RECONSENT:
        raise HTTPException(status_code=409, detail="La connexion n'attend pas de re-consentement")
    from app.connectors.capabilities import granted_capabilities
    from app.connectors.registry import get_provider

    manifest = get_provider(connection.provider)
    capabilities = sorted(granted_capabilities(connection)) or sorted(manifest.capabilities)
    try:
        url = build_authorization_url(
            connection.provider,
            tenant_slug=ctx.slug,
            user_id=auth.user.id,
            kind=connection.kind,
            capabilities=capabilities,
            return_host=request.headers.get("host", ""),
            connection_id=connection.id,
        )
    except (ConnectorOAuthError, service.ConnectorError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return AuthorizationUrlResponse(authorization_url=url)


@router.delete("/{connection_id}", operation_id="revokeConnector")
async def connector_revoke(
    connection_id: uuid.UUID,
    ctx: Annotated[TenantContext, Depends(require_permission("core.connectors.manage"))],
    auth: Annotated[CurrentAuth, Depends(current_auth)],
    control_session: ControlSession,
    tenant_session: TenantSession,
) -> StatusResponse:
    """Révocation : best-effort chez le provider, suppression locale garantie (D9)."""
    connection = await tenant_session.get(ConnectorConnection, connection_id)
    if connection is None:
        raise HTTPException(status_code=404, detail="Connexion introuvable")
    if connection.status is ConnectionStatus.REVOKED:
        return StatusResponse()

    await service.revoke_connection(tenant_session, connection)
    await tenant_session.execute(
        delete(ConnectorSubscription).where(ConnectorSubscription.connection_id == connection.id)
    )
    await record_audit_event(
        tenant_session,
        action="connector.revoked",
        resource_type="connector_connection",
        resource_id=str(connection.id),
        payload={"provider": connection.provider.value, "account": connection.account_label},
        actor_user_id=auth.user.id,
        actor_label=auth.user.display_name or auth.user.email,
    )
    await tenant_session.commit()

    # La route de webhook control-plane ne sert plus à rien : supprimée.
    await control_session.execute(
        delete(WebhookRoute).where(WebhookRoute.connection_id == connection.id)
    )
    await control_session.commit()
    return StatusResponse()
