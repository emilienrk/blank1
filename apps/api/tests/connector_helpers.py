"""Helpers partagés des tests connecteurs (Phase 5).

Faux providers locaux (manifests pointant sur des endpoints internes servis par
un `httpx.MockTransport`), création de connexions directement en DB tenant,
fakeredis pour le throttle et les verrous. Aucun test ne touche un vrai provider
(comme le faux OIDC de la Phase 2).
"""

# TestClient/httpx exposent des membres partiellement typés.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx

from app.connectors import service, throttle
from app.connectors.registry import (
    CAPABILITY_CALENDAR,
    CAPABILITY_MAIL,
    ProviderManifest,
)
from app.connectors.tenant_models import (
    ConnectionKind,
    ConnectionStatus,
    ConnectorConnection,
    ConnectorProvider,
)
from app.tenancy.context import TenantContext, tenant_context
from app.tenancy.engine_manager import get_engine_manager
from app.tenancy.models import Tenant

GOOGLE_SCOPES = [
    "openid",
    "email",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar",
]
MICROSOFT_SCOPES = [
    "openid",
    "email",
    "offline_access",
    "Mail.Read",
    "Mail.Send",
    "Calendars.ReadWrite",
]


def fake_google_manifest(base: str = "https://fake.google.test") -> ProviderManifest:
    """Manifest Google dont tous les endpoints pointent sur un hôte local mocké."""
    return ProviderManifest(
        provider=ConnectorProvider.GOOGLE,
        authorization_endpoint=f"{base}/authorize",
        token_endpoint=f"{base}/token",
        account_info_endpoint=f"{base}/userinfo",
        parse_account_label=lambda payload: payload.get("email"),
        base_scopes=("openid", "email"),
        capability_scopes={
            CAPABILITY_MAIL: (
                "https://www.googleapis.com/auth/gmail.readonly",
                "https://www.googleapis.com/auth/gmail.send",
            ),
            CAPABILITY_CALENDAR: ("https://www.googleapis.com/auth/calendar",),
        },
        api_base_url=f"{base}/api",
        revoke_endpoint=f"{base}/revoke",
        authorization_extra_params={"access_type": "offline", "prompt": "consent"},
        requests_per_minute=240,
        subscription_ttl_hours=7 * 24,
    )


def fake_microsoft_manifest(base: str = "https://fake.microsoft.test") -> ProviderManifest:
    return ProviderManifest(
        provider=ConnectorProvider.MICROSOFT,
        authorization_endpoint=f"{base}/authorize",
        token_endpoint=f"{base}/token",
        account_info_endpoint=f"{base}/me",
        parse_account_label=lambda payload: payload.get("mail") or payload.get("userPrincipalName"),
        base_scopes=("openid", "email", "offline_access"),
        capability_scopes={
            CAPABILITY_MAIL: ("Mail.Read", "Mail.Send"),
            CAPABILITY_CALENDAR: ("Calendars.ReadWrite",),
        },
        api_base_url=f"{base}/graph",
        revoke_endpoint=None,
        authorization_extra_params={},
        requests_per_minute=600,
        subscription_ttl_hours=3 * 24,
    )


def install_fake_transport(
    monkeypatch: object, handler: Callable[[httpx.Request], httpx.Response]
) -> None:
    """Remplace la frontière HTTP sortante des connecteurs par un transport mocké."""
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(  # type: ignore[attr-defined]
        service, "_http_client_factory", lambda: httpx.AsyncClient(transport=transport)
    )


def install_fake_valkey(monkeypatch: object) -> None:
    """fakeredis pour le throttle + les verrous ; sleep instantané (pas d'attente CI)."""
    import fakeredis.aioredis

    throttle.set_valkey_client(fakeredis.aioredis.FakeRedis())

    async def _instant_sleep(_: float) -> None:
        return None

    throttle.set_sleep(_instant_sleep)


def reset_connector_throttle() -> None:
    throttle.set_valkey_client(None)
    import asyncio

    throttle.set_sleep(asyncio.sleep)


def ctx_for(tenant: Tenant) -> TenantContext:
    return TenantContext(
        tenant_id=tenant.id,
        slug=tenant.slug,
        state=tenant.state,
        db_name=tenant.db_name,
        db_host=tenant.db_host,
        role=None,
    )


async def create_connection(
    tenant: Tenant,
    *,
    provider: ConnectorProvider = ConnectorProvider.GOOGLE,
    kind: ConnectionKind = ConnectionKind.TENANT,
    account_label: str = "contact@acme.test",
    scopes: list[str] | None = None,
    access_token: str = "access-initial",
    refresh_token: str = "refresh-initial",
    expires_in: int = 3600,
    status: ConnectionStatus = ConnectionStatus.ACTIVE,
    user_id: uuid.UUID | None = None,
) -> ConnectorConnection:
    """Insère une connexion (tokens chiffrés) directement en DB tenant."""
    default_scopes = GOOGLE_SCOPES if provider is ConnectorProvider.GOOGLE else MICROSOFT_SCOPES
    with tenant_context(ctx_for(tenant)):
        async with get_engine_manager().session(ctx_for(tenant)) as session:
            connection = ConnectorConnection(
                provider=provider,
                kind=kind,
                user_id=user_id,
                account_label=account_label,
                scopes=scopes if scopes is not None else default_scopes,
                access_token_enc=service.encrypt_token(access_token),
                refresh_token_enc=service.encrypt_token(refresh_token),
                access_token_expires_at=datetime.now(UTC) + timedelta(seconds=expires_in),
                status=status,
            )
            session.add(connection)
            await session.commit()
            await session.refresh(connection)
            return connection


async def load_connection(tenant: Tenant, connection_id: uuid.UUID) -> ConnectorConnection:
    with tenant_context(ctx_for(tenant)):
        async with get_engine_manager().session(ctx_for(tenant)) as session:
            connection = await session.get(ConnectorConnection, connection_id)
            assert connection is not None
            return connection
