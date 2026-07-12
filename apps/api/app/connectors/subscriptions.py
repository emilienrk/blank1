"""Subscriptions webhook côté providers (Phase 5 T8).

Création (post-connexion, hors requête HTTP) et renouvellement (beat) des
abonnements de notification : subscriptions Microsoft Graph (~3 j, renouvelées
par PATCH) et channels Google Calendar (~7 j, non renouvelables — recréés).
Gmail ne notifie que via Cloud Pub/Sub : la capability mail Google reste sans
webhook dans cette phase (documenté au README, risque n°4 du plan).

Le `client_state` remis au provider est un secret aléatoire stocké HACHÉ
(sha256) : il authentifie chaque notification entrante (invariant n°3).
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.tokens import generate_token, hash_token
from app.connectors.capabilities import granted_capabilities
from app.connectors.client_base import fresh_access_token, provider_http_request
from app.connectors.registry import (
    CAPABILITY_CALENDAR,
    CAPABILITY_MAIL,
    get_provider,
)
from app.connectors.tenant_models import (
    ConnectorConnection,
    ConnectorProvider,
    ConnectorSubscription,
)
from app.core.config import get_settings

logger = structlog.get_logger()

# Renouvellement quand la subscription expire sous ce délai (beat horaire).
RENEWAL_LEAD = timedelta(hours=24)

_GRAPH_RESOURCES = {CAPABILITY_MAIL: "/me/messages", CAPABILITY_CALENDAR: "/me/events"}


def webhook_url(provider: ConnectorProvider, route_key: str) -> str:
    base = get_settings().connector_webhook_base
    return f"{base}/api/v1/webhooks/{provider.value}/{route_key}"


def _parse_expiration(value: Any, default: datetime) -> datetime:
    if isinstance(value, str) and value:
        if value.isdigit():  # Google : epoch millisecondes
            return datetime.fromtimestamp(int(value) / 1000, tz=UTC)
        from app.connectors.providers.microsoft.mail import parse_graph_datetime

        return parse_graph_datetime(value)
    return default


async def _create_google_channel(
    session: AsyncSession,
    connection: ConnectorConnection,
    route_key: str,
) -> ConnectorSubscription:
    """Channel Google Calendar (events.watch) — le seul webhook Google de la phase."""
    manifest = get_provider(connection.provider)
    client_state = generate_token()
    channel_id = str(uuid.uuid4())
    ttl_seconds = manifest.subscription_ttl_hours * 3600
    access_token = await fresh_access_token(session, connection)
    response = await provider_http_request(
        manifest,
        connection,
        "POST",
        f"{manifest.api_base_url}/calendar/v3/calendars/primary/events/watch",
        access_token=access_token,
        json={
            "id": channel_id,
            "type": "web_hook",
            "address": webhook_url(connection.provider, route_key),
            "token": client_state,
            "params": {"ttl": str(ttl_seconds)},
        },
    )
    payload: dict[str, Any] = response.json()
    subscription = ConnectorSubscription(
        connection_id=connection.id,
        capability=CAPABILITY_CALENDAR,
        provider_subscription_id=channel_id,
        resource="calendar/primary/events",
        expires_at=_parse_expiration(
            payload.get("expiration"), datetime.now(UTC) + timedelta(seconds=ttl_seconds)
        ),
        client_state_hash=hash_token(client_state),
        provider_data={"resource_id": str(payload.get("resourceId", ""))},
    )
    session.add(subscription)
    await session.flush()
    return subscription


async def _stop_google_channel(
    session: AsyncSession, connection: ConnectorConnection, subscription: ConnectorSubscription
) -> None:
    """Arrêt best-effort de l'ancien channel après recréation."""
    manifest = get_provider(connection.provider)
    try:
        access_token = await fresh_access_token(session, connection)
        await provider_http_request(
            manifest,
            connection,
            "POST",
            f"{manifest.api_base_url}/calendar/v3/channels/stop",
            access_token=access_token,
            json={
                "id": subscription.provider_subscription_id,
                "resourceId": subscription.provider_data.get("resource_id", ""),
            },
        )
    except Exception as exc:
        logger.info(
            "connector_channel_stop_failed",
            connection_id=str(connection.id),
            error=exc.__class__.__name__,
        )


async def _create_graph_subscription(
    session: AsyncSession,
    connection: ConnectorConnection,
    route_key: str,
    capability: str,
) -> ConnectorSubscription:
    manifest = get_provider(connection.provider)
    client_state = generate_token()
    expires_at = datetime.now(UTC) + timedelta(hours=manifest.subscription_ttl_hours)
    access_token = await fresh_access_token(session, connection)
    response = await provider_http_request(
        manifest,
        connection,
        "POST",
        f"{manifest.api_base_url}/subscriptions",
        access_token=access_token,
        json={
            "changeType": "created,updated",
            "notificationUrl": webhook_url(connection.provider, route_key),
            "resource": _GRAPH_RESOURCES[capability],
            "expirationDateTime": expires_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "clientState": client_state,
        },
    )
    payload: dict[str, Any] = response.json()
    subscription = ConnectorSubscription(
        connection_id=connection.id,
        capability=capability,
        provider_subscription_id=str(payload.get("id", "")),
        resource=_GRAPH_RESOURCES[capability],
        expires_at=_parse_expiration(payload.get("expirationDateTime"), expires_at),
        client_state_hash=hash_token(client_state),
    )
    session.add(subscription)
    await session.flush()
    return subscription


async def ensure_subscriptions(
    session: AsyncSession, connection: ConnectorConnection, route_key: str
) -> None:
    """Crée les subscriptions manquantes pour les capabilities consenties."""
    existing = {
        subscription.capability
        for subscription in (
            await session.scalars(
                select(ConnectorSubscription).where(
                    ConnectorSubscription.connection_id == connection.id
                )
            )
        ).all()
    }
    for capability in sorted(granted_capabilities(connection)):
        if capability in existing:
            continue
        if connection.provider is ConnectorProvider.GOOGLE:
            if capability == CAPABILITY_MAIL:
                # Gmail push = Cloud Pub/Sub uniquement : pas de webhook direct.
                logger.info(
                    "connector_subscription_unsupported",
                    provider="google",
                    capability=capability,
                )
                continue
            await _create_google_channel(session, connection, route_key)
        else:
            await _create_graph_subscription(session, connection, route_key, capability)
        logger.info(
            "connector_subscription_created",
            provider=connection.provider.value,
            connection_id=str(connection.id),
            capability=capability,
        )


async def renew_subscription(
    session: AsyncSession,
    connection: ConnectorConnection,
    subscription: ConnectorSubscription,
    route_key: str,
) -> None:
    """Renouvelle une subscription : PATCH Graph, recréation de channel Google."""
    manifest = get_provider(connection.provider)
    if connection.provider is ConnectorProvider.MICROSOFT:
        expires_at = datetime.now(UTC) + timedelta(hours=manifest.subscription_ttl_hours)
        access_token = await fresh_access_token(session, connection)
        await provider_http_request(
            manifest,
            connection,
            "PATCH",
            f"{manifest.api_base_url}/subscriptions/{subscription.provider_subscription_id}",
            access_token=access_token,
            json={"expirationDateTime": expires_at.strftime("%Y-%m-%dT%H:%M:%SZ")},
        )
        subscription.expires_at = expires_at
        await session.flush()
    else:
        # Les channels Google ne se prolongent pas : recréer puis stopper l'ancien.
        replacement = await _create_google_channel(session, connection, route_key)
        await _stop_google_channel(session, connection, subscription)
        await session.delete(subscription)
        await session.flush()
        logger.info(
            "connector_channel_recreated",
            connection_id=str(connection.id),
            subscription_id=str(replacement.id),
        )
