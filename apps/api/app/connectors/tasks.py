"""Tâches Celery des connecteurs (Phase 5 T6/T8).

- Refresh proactif des tokens (beat 5 min, décision D5 : verrou par connexion) ;
- Renouvellement des subscriptions webhook (beat horaire, T8) ;
- Traitement des événements webhook (dispatchés par la route de réception) ;
- Création des subscriptions post-connexion (dispatchée par le callback OAuth).

Toutes posent explicitement le contexte tenant (invariant racine n°1) — même
pattern que `app.gdpr.tasks`.
"""

# Celery n'expose pas de types (voir app/worker.py).
# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUntypedFunctionDecorator=false
# pyright: reportUnknownVariableType=false, reportCallIssue=false

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import structlog
from celery import shared_task
from sqlalchemy import select

from app.audit.service import record_audit_event
from app.connectors import service, subscriptions, throttle
from app.connectors.models import WebhookRoute
from app.connectors.tenant_models import (
    ConnectionStatus,
    ConnectorConnection,
    ConnectorProvider,
    ConnectorSubscription,
)
from app.connectors.webhooks import ConnectorEvent, event_handlers
from app.core.config import get_settings
from app.core.db import dispose_control_engine, get_control_sessionmaker
from app.tenancy.context import TenantContext, tenant_context
from app.tenancy.engine_manager import dispose_engine_manager, get_engine_manager
from app.tenancy.models import Tenant, TenantState

logger = structlog.get_logger()


def _context_for(tenant: Tenant) -> TenantContext:
    return TenantContext(
        tenant_id=tenant.id,
        slug=tenant.slug,
        state=tenant.state,
        db_name=tenant.db_name,
        db_host=tenant.db_host,
        role=None,
    )


async def _active_tenants() -> list[Tenant]:
    async with get_control_sessionmaker()() as control_session:
        return list(
            (
                await control_session.scalars(
                    select(Tenant).where(Tenant.state == TenantState.ACTIVE)
                )
            ).all()
        )


async def _dispose_engines() -> None:
    # Pools asyncpg liés à leur event loop (cf. app/gdpr/tasks.py).
    await dispose_control_engine()
    await dispose_engine_manager()


# --- Refresh proactif des tokens (T6, beat 5 min) ---


async def refresh_expiring_tokens() -> dict[str, dict[str, int]]:
    """Itère les tenants actifs et rafraîchit les connexions expirant sous
    `connector_refresh_lead_minutes` (verrou Valkey par connexion, D5)."""
    lead = timedelta(minutes=get_settings().connector_refresh_lead_minutes)
    manager = get_engine_manager()
    report: dict[str, dict[str, int]] = {}
    for tenant in await _active_tenants():
        ctx = _context_for(tenant)
        refreshed = skipped = failed = 0
        with tenant_context(ctx):
            structlog.contextvars.bind_contextvars(tenant=ctx.slug)
            try:
                async with manager.session(ctx) as session:
                    connections = (
                        await session.scalars(
                            select(ConnectorConnection).where(
                                ConnectorConnection.status == ConnectionStatus.ACTIVE
                            )
                        )
                    ).all()
                    for connection in connections:
                        if not service.access_token_expiring(connection, lead):
                            continue
                        try:
                            if await service.refresh_connection(session, connection, block=False):
                                refreshed += 1
                            else:
                                skipped += 1
                        except (
                            throttle.ProviderResponseError,
                            throttle.ProviderUnavailable,
                            httpx.HTTPError,
                        ):
                            # last_error posé par le service ; le prochain beat réessaie.
                            failed += 1
                        await session.commit()
            finally:
                structlog.contextvars.unbind_contextvars("tenant")
        report[tenant.slug] = {"refreshed": refreshed, "skipped": skipped, "failed": failed}
        if refreshed or skipped or failed:
            logger.info("connector_refresh_report", tenant=tenant.slug, **report[tenant.slug])
    return report


@shared_task(name="connectors.refresh_expiring_tokens")
def refresh_expiring_tokens_task() -> None:
    async def run() -> None:
        try:
            await refresh_expiring_tokens()
        finally:
            await _dispose_engines()

    asyncio.run(run())


# --- Renouvellement des subscriptions webhook (T8, beat horaire) ---


async def _route_key_for(connection_id: uuid.UUID) -> str | None:
    async with get_control_sessionmaker()() as control_session:
        route = await control_session.scalar(
            select(WebhookRoute).where(WebhookRoute.connection_id == connection_id)
        )
        return route.route_key if route is not None else None


async def renew_expiring_subscriptions() -> dict[str, dict[str, int]]:
    """Renouvelle les subscriptions expirant sous `RENEWAL_LEAD` (verrou par
    connexion, même pattern que le refresh) ; échec définitif → santé dégradée
    + audit `connector.subscription_renewal_failed`."""
    manager = get_engine_manager()
    horizon = datetime.now(UTC) + subscriptions.RENEWAL_LEAD
    report: dict[str, dict[str, int]] = {}
    for tenant in await _active_tenants():
        ctx = _context_for(tenant)
        renewed = failed = 0
        with tenant_context(ctx):
            structlog.contextvars.bind_contextvars(tenant=ctx.slug)
            try:
                async with manager.session(ctx) as session:
                    expiring = (
                        await session.scalars(
                            select(ConnectorSubscription).where(
                                ConnectorSubscription.expires_at <= horizon
                            )
                        )
                    ).all()
                    for subscription in expiring:
                        connection = await session.get(
                            ConnectorConnection, subscription.connection_id
                        )
                        if connection is None or connection.status is not ConnectionStatus.ACTIVE:
                            continue
                        lock_name = f"renew:{subscription.id}"
                        lock_token = await throttle.acquire_lock(lock_name)
                        if lock_token is None:
                            continue
                        try:
                            route_key = await _route_key_for(connection.id)
                            if route_key is None:
                                continue
                            await subscriptions.renew_subscription(
                                session, connection, subscription, route_key
                            )
                            connection.health_checked_at = datetime.now(UTC)
                            renewed += 1
                        except Exception as exc:
                            failed += 1
                            connection.status = ConnectionStatus.ERROR
                            connection.last_error = (
                                f"Renouvellement de subscription échoué : {exc.__class__.__name__}"
                            )
                            connection.health_checked_at = datetime.now(UTC)
                            await record_audit_event(
                                session,
                                action="connector.subscription_renewal_failed",
                                resource_type="connector_subscription",
                                resource_id=str(subscription.id),
                                payload={
                                    "provider": connection.provider.value,
                                    "capability": subscription.capability,
                                },
                            )
                            logger.warning(
                                "connector_subscription_renewal_failed",
                                connection_id=str(connection.id),
                                capability=subscription.capability,
                                error=exc.__class__.__name__,
                            )
                        finally:
                            await throttle.release_lock(lock_name, lock_token)
                        await session.commit()
            finally:
                structlog.contextvars.unbind_contextvars("tenant")
        report[tenant.slug] = {"renewed": renewed, "failed": failed}
    return report


@shared_task(name="connectors.renew_subscriptions")
def renew_subscriptions_task() -> None:
    async def run() -> None:
        try:
            await renew_expiring_subscriptions()
        finally:
            await _dispose_engines()

    asyncio.run(run())


# --- Création des subscriptions post-connexion (dispatchée par le callback T3) ---


async def sync_connection_subscriptions(slug: str, connection_id: uuid.UUID) -> None:
    tenants = [t for t in await _active_tenants() if t.slug == slug]
    if not tenants:
        return
    ctx = _context_for(tenants[0])
    route_key = await _route_key_for(connection_id)
    if route_key is None:
        return
    with tenant_context(ctx):
        async with get_engine_manager().session(ctx) as session:
            connection = await session.get(ConnectorConnection, connection_id)
            if connection is None or connection.status is not ConnectionStatus.ACTIVE:
                return
            try:
                await subscriptions.ensure_subscriptions(session, connection, route_key)
            except Exception as exc:
                connection.last_error = (
                    f"Création de subscription échouée : {exc.__class__.__name__}"
                )
                logger.warning(
                    "connector_subscription_create_failed",
                    connection_id=str(connection_id),
                    error=exc.__class__.__name__,
                )
            await session.commit()


@shared_task(name="connectors.sync_subscriptions")
def sync_subscriptions_task(slug: str, connection_id: str) -> None:
    async def run() -> None:
        try:
            await sync_connection_subscriptions(slug, uuid.UUID(connection_id))
        finally:
            await _dispose_engines()

    asyncio.run(run())


async def enqueue_subscription_sync(slug: str, connection_id: uuid.UUID) -> None:
    """Frontière de dispatch vers Celery — remplacée en test (pas de broker)."""
    sync_subscriptions_task.delay(slug, str(connection_id))


# --- Événements webhook (T8, décision D7) ---


async def process_connector_event(
    slug: str,
    connection_id: uuid.UUID,
    provider: str,
    capability: str,
    change_type: str,
    resource_id: str | None,
) -> None:
    """Pose le contexte tenant, journalise + audite l'événement normalisé, puis
    livre aux handlers du registre interne (consommateurs réels : Phase 7)."""
    tenants = [t for t in await _active_tenants() if t.slug == slug]
    if not tenants:
        return
    ctx = _context_for(tenants[0])
    event = ConnectorEvent(
        provider=ConnectorProvider(provider),
        capability=capability,
        connection_id=connection_id,
        change_type=change_type,
        resource_id=resource_id,
    )
    with tenant_context(ctx):
        structlog.contextvars.bind_contextvars(tenant=ctx.slug)
        try:
            async with get_engine_manager().session(ctx) as session:
                connection = await session.get(ConnectorConnection, connection_id)
                if connection is None:
                    return
                # La santé dérive des opérations réelles (D8) : un webhook valide
                # prouve que la chaîne provider → plateforme fonctionne.
                connection.health_checked_at = datetime.now(UTC)
                await record_audit_event(
                    session,
                    action="connector.event_received",
                    resource_type="connector_connection",
                    resource_id=str(connection_id),
                    payload={
                        "provider": provider,
                        "capability": capability,
                        "change_type": change_type,
                        "resource_id": resource_id,
                    },
                )
                await session.commit()
            logger.info(
                "connector_event_received",
                provider=provider,
                capability=capability,
                connection_id=str(connection_id),
                change_type=change_type,
            )
            for handler in event_handlers(capability):
                await handler(event)
        finally:
            structlog.contextvars.unbind_contextvars("tenant")


@shared_task(name="connectors.event_received")
def connector_event_received_task(
    slug: str,
    connection_id: str,
    provider: str,
    capability: str,
    change_type: str,
    resource_id: str | None,
) -> None:
    async def run() -> None:
        try:
            await process_connector_event(
                slug, uuid.UUID(connection_id), provider, capability, change_type, resource_id
            )
        finally:
            await _dispose_engines()

    asyncio.run(run())


async def enqueue_event(
    slug: str,
    connection_id: uuid.UUID,
    provider: str,
    capability: str,
    change_type: str,
    resource_id: str | None,
) -> None:
    """Frontière de dispatch vers Celery — remplacée en test (pas de broker)."""
    connector_event_received_task.delay(
        slug, str(connection_id), provider, capability, change_type, resource_id
    )
