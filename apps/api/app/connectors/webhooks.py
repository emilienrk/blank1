"""Webhooks entrants des providers (Phase 5 T8) — route anonyme, liste fermée.

Chaîne complète (décision D7) : réception validée → tâche Celery → contexte
tenant → événement normalisé journalisé + audité. Les consommateurs réels
arrivent en Phase 7 via le registre interne minimal `on_connector_event`.

Invariant n°3 : toute réception est authentifiée (echo `validationToken` +
`clientState` haché chez Microsoft, en-têtes de channel comparés chez Google)
AVANT toute action ; un webhook invalide reçoit une réponse 2xx neutre, sans
traitement ni log verbeux (pas d'oracle sur l'existence des routes).
"""

import uuid
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, cast

import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.tokens import hash_token
from app.connectors.models import WebhookRoute
from app.connectors.tenant_models import ConnectorProvider, ConnectorSubscription
from app.core.db import get_control_session
from app.tenancy.context import TenantContext, tenant_context
from app.tenancy.models import Tenant, TenantState
from app.tenancy.session import tenant_session

logger = structlog.get_logger()

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

ControlSession = Annotated[AsyncSession, Depends(get_control_session)]


class ConnectorEvent(BaseModel):
    """Événement normalisé minimal livré aux consommateurs (D7) : des
    identifiants et un type de changement — jamais de contenu métier."""

    provider: ConnectorProvider
    capability: str
    connection_id: uuid.UUID
    change_type: str
    resource_id: str | None = None


EventHandler = Callable[[ConnectorEvent], Awaitable[None]]

_event_handlers: dict[str, list[EventHandler]] = {}


def on_connector_event(capability: str, handler: EventHandler) -> None:
    """Registre interne minimal (décision D7) — premier client réel : Phase 7."""
    _event_handlers.setdefault(capability, []).append(handler)


def event_handlers(capability: str) -> list[EventHandler]:
    return list(_event_handlers.get(capability, []))


def reset_event_handlers() -> None:
    """Réinitialisation (tests)."""
    _event_handlers.clear()


_NEUTRAL = {"status": "ok"}


async def _validated_notifications(
    tenant_session: AsyncSession,
    provider: ConnectorProvider,
    connection_id: uuid.UUID,
    request: Request,
) -> list[tuple[ConnectorSubscription, str, str | None]]:
    """Notifications authentifiées : (subscription, change_type, resource_id)."""
    subscriptions = {
        subscription.provider_subscription_id: subscription
        for subscription in (
            await tenant_session.scalars(
                select(ConnectorSubscription).where(
                    ConnectorSubscription.connection_id == connection_id
                )
            )
        ).all()
    }
    validated: list[tuple[ConnectorSubscription, str, str | None]] = []

    if provider is ConnectorProvider.MICROSOFT:
        try:
            raw: Any = await request.json()
        except ValueError:
            return []
        if not isinstance(raw, dict):
            return []
        body = cast(dict[str, Any], raw)
        value = body.get("value", [])
        if not isinstance(value, list):
            return []
        for item in cast(list[Any], value):
            if not isinstance(item, dict):
                continue
            notification = cast(dict[str, Any], item)
            subscription = subscriptions.get(str(notification.get("subscriptionId", "")))
            client_state = notification.get("clientState")
            if (
                subscription is None
                or not isinstance(client_state, str)
                or hash_token(client_state) != subscription.client_state_hash
            ):
                continue
            resource_data = notification.get("resourceData")
            resource_id = (
                str(cast(dict[str, Any], resource_data).get("id"))
                if isinstance(resource_data, dict)
                else None
            )
            validated.append(
                (subscription, str(notification.get("changeType", "updated")), resource_id)
            )
        return validated

    # Google : l'authentification passe par les en-têtes du channel.
    channel_id = request.headers.get("x-goog-channel-id", "")
    channel_token = request.headers.get("x-goog-channel-token", "")
    resource_state = request.headers.get("x-goog-resource-state", "")
    subscription = subscriptions.get(channel_id)
    if (
        subscription is None
        or not channel_token
        or hash_token(channel_token) != subscription.client_state_hash
    ):
        return []
    if resource_state == "sync":
        # Message de confirmation d'abonnement : authentique mais sans événement.
        return []
    validated.append(
        (subscription, resource_state or "updated", request.headers.get("x-goog-resource-id"))
    )
    return validated


@router.post("/{provider}/{route_key}", operation_id="connectorWebhook")
async def receive_webhook(
    provider: ConnectorProvider,
    route_key: str,
    request: Request,
    control_session: ControlSession,
) -> Response:
    """Traitement minimal : validation d'origine, accusé immédiat, tâche Celery."""
    # Handshake de validation Microsoft Graph : echo du token, en texte brut.
    validation_token = request.query_params.get("validationToken")
    if validation_token is not None:
        return PlainTextResponse(validation_token)

    route = await control_session.scalar(
        select(WebhookRoute).where(
            WebhookRoute.route_key == route_key, WebhookRoute.provider == provider.value
        )
    )
    if route is None:
        return _neutral_response()
    tenant = await control_session.get(Tenant, route.tenant_id)
    if tenant is None or tenant.state is not TenantState.ACTIVE or tenant.deleted_at:
        return _neutral_response()

    ctx = TenantContext(tenant_id=tenant.id, slug=tenant.slug)
    with tenant_context(ctx):
        async with tenant_session() as scoped_session:
            notifications = await _validated_notifications(
                scoped_session, provider, route.connection_id, request
            )

    from app.connectors import tasks as connector_tasks

    for subscription, change_type, resource_id in notifications:
        await connector_tasks.enqueue_event(
            tenant.slug,
            route.connection_id,
            provider.value,
            subscription.capability,
            change_type,
            resource_id,
        )
    return _neutral_response()


def _neutral_response() -> Response:
    """Réponse identique quel que soit le sort de la notification (invariant n°3)."""
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=202, content=_NEUTRAL)
