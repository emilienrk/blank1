# TestClient/httpx exposent des membres partiellement typés.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
"""Webhooks entrants + renouvellement des subscriptions (Phase 5 T8).

Validation Microsoft (echo validationToken) ; notification avec bon clientState →
tâche publiée avec le bon tenant ; clientState faux ou route_key inconnu →
réponse neutre, aucune tâche ; renouvellement avant expiration ; échec définitif
→ santé dégradée.
"""

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth.tokens import generate_token, hash_token
from app.connectors.models import WebhookRoute
from app.connectors.registry import override_provider
from app.connectors.tasks import renew_expiring_subscriptions
from app.connectors.tenant_models import (
    ConnectionStatus,
    ConnectorProvider,
    ConnectorSubscription,
)
from app.core.config import Settings
from app.core.db import get_control_sessionmaker
from app.main import create_app
from app.tenancy.context import tenant_context
from app.tenancy.provisioning import provision_tenant
from app.tenancy.session import tenant_session
from tests.conftest import requires_postgres
from tests.connector_helpers import (
    create_connection,
    ctx_for,
    fake_microsoft_manifest,
    install_fake_transport,
    install_fake_valkey,
    load_connection,
    reset_connector_throttle,
)
from tests.helpers import reset_db_engines

pytestmark = requires_postgres


async def _add_route(tenant_id: uuid.UUID, connection_id: uuid.UUID, provider: str) -> str:
    route_key = generate_token()
    async with get_control_sessionmaker()() as session:
        session.add(
            WebhookRoute(
                route_key=route_key,
                provider=provider,
                tenant_id=tenant_id,
                connection_id=connection_id,
            )
        )
        await session.commit()
    return route_key


async def _add_subscription(
    tenant: object,
    connection_id: uuid.UUID,
    *,
    client_state: str,
    provider_subscription_id: str = "sub-1",
    capability: str = "mail",
    expires_at: datetime | None = None,
) -> uuid.UUID:
    with tenant_context(ctx_for(tenant)):  # type: ignore[arg-type]
        async with tenant_session() as session:  # type: ignore[arg-type]
            subscription = ConnectorSubscription(
                connection_id=connection_id,
                capability=capability,
                provider_subscription_id=provider_subscription_id,
                resource="/me/messages",
                expires_at=expires_at or datetime.now(UTC) + timedelta(days=2),
                client_state_hash=hash_token(client_state),
            )
            session.add(subscription)
            await session.commit()
            await session.refresh(subscription)
            return subscription.id


def test_microsoft_validation_token_is_echoed() -> None:
    with TestClient(create_app()) as client:
        response = client.post(
            f"/api/v1/webhooks/microsoft/{generate_token()}?validationToken=abc123",
        )
        assert response.status_code == 200
        assert response.text == "abc123"


async def test_valid_notification_dispatches_task_with_tenant(
    db_env: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = await provision_tenant("acme", "ACME")
    connection = await create_connection(tenant, provider=ConnectorProvider.MICROSOFT)
    client_state = generate_token()
    await _add_subscription(tenant, connection.id, client_state=client_state)
    route_key = await _add_route(tenant.id, connection.id, "microsoft")
    await reset_db_engines()

    dispatched: list[tuple[object, ...]] = []

    async def fake_enqueue(*args: object) -> None:
        dispatched.append(args)

    import app.connectors.tasks as connector_tasks

    monkeypatch.setattr(connector_tasks, "enqueue_event", fake_enqueue)

    with TestClient(create_app()) as client:
        response = client.post(
            f"/api/v1/webhooks/microsoft/{route_key}",
            json={
                "value": [
                    {
                        "subscriptionId": "sub-1",
                        "clientState": client_state,
                        "changeType": "created",
                        "resourceData": {"id": "msg-99"},
                    }
                ]
            },
        )
        assert response.status_code == 202

    assert len(dispatched) == 1
    # (slug, connection_id, provider, capability, change_type, resource_id)
    assert dispatched[0][0] == "acme"
    assert dispatched[0][2] == "microsoft"
    assert dispatched[0][4] == "created"


async def test_bad_client_state_is_neutral_and_dispatches_nothing(
    db_env: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = await provision_tenant("acme", "ACME")
    connection = await create_connection(tenant, provider=ConnectorProvider.MICROSOFT)
    await _add_subscription(tenant, connection.id, client_state=generate_token())
    route_key = await _add_route(tenant.id, connection.id, "microsoft")
    await reset_db_engines()

    dispatched: list[tuple[object, ...]] = []

    async def fake_enqueue(*args: object) -> None:
        dispatched.append(args)

    import app.connectors.tasks as connector_tasks

    monkeypatch.setattr(connector_tasks, "enqueue_event", fake_enqueue)

    with TestClient(create_app()) as client:
        response = client.post(
            f"/api/v1/webhooks/microsoft/{route_key}",
            json={"value": [{"subscriptionId": "sub-1", "clientState": "WRONG"}]},
        )
        # Réponse neutre (pas d'oracle) ; aucune tâche.
        assert response.status_code == 202
    assert dispatched == []


async def test_unknown_route_key_is_neutral(
    db_env: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    await provision_tenant("acme", "ACME")
    await reset_db_engines()

    dispatched: list[tuple[object, ...]] = []

    async def fake_enqueue(*args: object) -> None:
        dispatched.append(args)

    import app.connectors.tasks as connector_tasks

    monkeypatch.setattr(connector_tasks, "enqueue_event", fake_enqueue)

    with TestClient(create_app()) as client:
        response = client.post(
            f"/api/v1/webhooks/microsoft/{generate_token()}",
            json={"value": [{"subscriptionId": "sub-1", "clientState": "x"}]},
        )
        assert response.status_code == 202
    assert dispatched == []


async def test_subscription_renewed_before_expiration(
    db_env: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake_valkey(monkeypatch)
    tenant = await provision_tenant("acme", "ACME")
    connection = await create_connection(tenant, provider=ConnectorProvider.MICROSOFT)
    client_state = generate_token()
    # Expire dans 1h : sous le RENEWAL_LEAD de 24h.
    subscription_id = await _add_subscription(
        tenant,
        connection.id,
        client_state=client_state,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    await _add_route(tenant.id, connection.id, "microsoft")
    await reset_db_engines()
    install_fake_valkey(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PATCH" and "/subscriptions/" in str(request.url):
            return httpx.Response(200, json={"id": "sub-1"})
        return httpx.Response(404)

    install_fake_transport(monkeypatch, handler)

    try:
        with override_provider(fake_microsoft_manifest()):
            report = await renew_expiring_subscriptions()
        assert report["acme"]["renewed"] == 1
        with tenant_context(ctx_for(tenant)):
            async with tenant_session() as session:
                subscription = await session.get(ConnectorSubscription, subscription_id)
                assert subscription is not None
                # Nouvelle expiration repoussée bien au-delà d'1h.
                assert subscription.expires_at > datetime.now(UTC) + timedelta(hours=24)
    finally:
        reset_connector_throttle()


async def test_process_event_audits_and_invokes_registered_handler(
    db_env: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D7 : l'événement normalisé est audité, la santé rafraîchie, et livré aux
    handlers du registre interne (premier client réel : Phase 7)."""
    from app.connectors.tasks import process_connector_event
    from app.connectors.webhooks import (
        ConnectorEvent,
        on_connector_event,
        reset_event_handlers,
    )

    tenant = await provision_tenant("acme", "ACME")
    connection = await create_connection(tenant, provider=ConnectorProvider.MICROSOFT)
    await reset_db_engines()

    received: list[ConnectorEvent] = []

    async def handler(event: ConnectorEvent) -> None:
        received.append(event)

    reset_event_handlers()
    on_connector_event("mail", handler)
    try:
        await process_connector_event(
            "acme", connection.id, "microsoft", "mail", "created", "msg-1"
        )
    finally:
        reset_event_handlers()

    assert len(received) == 1
    assert received[0].connection_id == connection.id
    assert received[0].change_type == "created"

    reloaded = await load_connection(tenant, connection.id)
    assert reloaded.health_checked_at is not None
    with tenant_context(ctx_for(tenant)):
        async with tenant_session() as session:
            from app.audit.tenant_models import AuditEvent

            actions = [e.action for e in (await session.scalars(select(AuditEvent))).all()]
            assert "connector.event_received" in actions


async def test_renewal_failure_degrades_health(
    db_env: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake_valkey(monkeypatch)
    tenant = await provision_tenant("acme", "ACME")
    connection = await create_connection(tenant, provider=ConnectorProvider.MICROSOFT)
    await _add_subscription(
        tenant,
        connection.id,
        client_state=generate_token(),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    await _add_route(tenant.id, connection.id, "microsoft")
    await reset_db_engines()
    install_fake_valkey(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        # PATCH échoue définitivement (4xx non récupérable).
        return httpx.Response(404, json={"error": "gone"})

    install_fake_transport(monkeypatch, handler)

    try:
        with override_provider(fake_microsoft_manifest()):
            report = await renew_expiring_subscriptions()
        assert report["acme"]["failed"] == 1
        reloaded = await load_connection(tenant, connection.id)
        assert reloaded.status is ConnectionStatus.ERROR
        assert reloaded.last_error is not None
    finally:
        reset_connector_throttle()
