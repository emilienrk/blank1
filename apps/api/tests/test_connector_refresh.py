# TestClient/httpx exposent des membres partiellement typés.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
"""Refresh des tokens de connecteurs (Phase 5 T6, décision D5).

Connexion proche d'expirer → refresh, tokens re-chiffrés ; verrou pris → seconde
tâche s'abstient ; invalid_grant → needs_reconsent + audit ; refresh à la volée
dans client_base sérialisé avec le périodique (même verrou).
"""

from collections.abc import Callable, Iterator

import httpx
import pytest
from sqlalchemy import select

from app.audit.tenant_models import AuditEvent
from app.connectors import service, throttle
from app.connectors.client_base import fresh_access_token
from app.connectors.registry import override_provider
from app.connectors.tasks import refresh_expiring_tokens
from app.connectors.tenant_models import ConnectionStatus, ConnectorConnection
from app.core.config import Settings, get_settings
from app.tenancy.context import tenant_context
from app.tenancy.engine_manager import get_engine_manager
from app.tenancy.provisioning import provision_tenant
from tests.conftest import requires_postgres
from tests.connector_helpers import (
    create_connection,
    ctx_for,
    fake_google_manifest,
    install_fake_transport,
    install_fake_valkey,
    load_connection,
    reset_connector_throttle,
)
from tests.helpers import reset_db_engines

pytestmark = requires_postgres


def _configure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_CONNECTOR_CLIENT_ID", "id")
    monkeypatch.setenv("GOOGLE_CONNECTOR_CLIENT_SECRET", "secret")
    get_settings.cache_clear()


def _refresh_handler(
    response: dict[str, object], status: int = 200
) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        if "/token" in str(request.url):
            return httpx.Response(status, json=response)
        return httpx.Response(404)

    return handler


@pytest.fixture(autouse=True)
def connector_valkey(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    install_fake_valkey(monkeypatch)
    yield
    reset_connector_throttle()


async def test_expiring_connection_is_refreshed_and_reencrypted(
    db_env: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(monkeypatch)
    tenant = await provision_tenant("acme", "ACME")
    # Access token déjà expiré → sous le seuil de refresh.
    connection = await create_connection(tenant, expires_in=-10)
    await reset_db_engines()
    install_fake_valkey(monkeypatch)  # nouveau client après reset des engines

    install_fake_transport(
        monkeypatch,
        _refresh_handler(
            {
                "access_token": "rotated-access",
                "refresh_token": "rotated-refresh",
                "expires_in": 3600,
            }
        ),
    )

    with override_provider(fake_google_manifest()):
        report = await refresh_expiring_tokens()

    assert report["acme"]["refreshed"] == 1
    refreshed = await load_connection(tenant, connection.id)
    assert refreshed.status is ConnectionStatus.ACTIVE
    assert service.decrypt_token(refreshed.access_token_enc) == "rotated-access"
    # Microsoft/Google font tourner le refresh token : le nouveau est stocké.
    assert service.decrypt_token(refreshed.refresh_token_enc) == "rotated-refresh"


async def test_invalid_grant_marks_needs_reconsent_and_audits(
    db_env: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(monkeypatch)
    tenant = await provision_tenant("acme", "ACME")
    connection = await create_connection(tenant, expires_in=-10)
    await reset_db_engines()
    install_fake_valkey(monkeypatch)

    install_fake_transport(monkeypatch, _refresh_handler({"error": "invalid_grant"}, status=400))

    with override_provider(fake_google_manifest()):
        report = await refresh_expiring_tokens()

    assert report["acme"]["refreshed"] == 0
    reloaded = await load_connection(tenant, connection.id)
    assert reloaded.status is ConnectionStatus.NEEDS_RECONSENT
    assert reloaded.last_error is not None

    # Audit connector.reconsent_required émis.
    with tenant_context(ctx_for(tenant)):
        async with get_engine_manager().session(ctx_for(tenant)) as session:
            actions = [e.action for e in (await session.scalars(select(AuditEvent))).all()]
            assert "connector.reconsent_required" in actions


async def test_held_lock_makes_periodic_refresh_abstain(
    db_env: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(monkeypatch)
    tenant = await provision_tenant("acme", "ACME")
    connection = await create_connection(tenant, expires_in=-10)
    await reset_db_engines()
    install_fake_valkey(monkeypatch)

    # Un autre worker détient déjà le verrou de refresh de cette connexion.
    held = await throttle.acquire_lock(f"refresh:{connection.id}")
    assert held is not None

    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={"access_token": "x", "expires_in": 3600})

    install_fake_transport(monkeypatch, handler)

    with override_provider(fake_google_manifest()):
        report = await refresh_expiring_tokens()

    # La tâche périodique s'abstient : verrou pris, aucun appel au provider.
    assert report["acme"]["skipped"] == 1
    assert called is False
    reloaded = await load_connection(tenant, connection.id)
    assert reloaded.status is ConnectionStatus.ACTIVE


async def test_on_the_fly_refresh_serialized_with_periodic(
    db_env: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """client_base rafraîchit à la volée quand l'access token expire — sous le
    MÊME verrou que le périodique : si le verrou n'est jamais libérable, l'appel
    finit par lever ProviderUnavailable (preuve qu'il attend le verrou)."""
    _configure(monkeypatch)
    tenant = await provision_tenant("acme", "ACME")
    connection = await create_connection(tenant, expires_in=-10)
    await reset_db_engines()
    install_fake_valkey(monkeypatch)

    # Verrou pris et jamais rendu : le refresh à la volée doit attendre en vain.
    held = await throttle.acquire_lock(f"refresh:{connection.id}", ttl_seconds=300)
    assert held is not None

    with override_provider(fake_google_manifest()), tenant_context(ctx_for(tenant)):
        async with get_engine_manager().session(ctx_for(tenant)) as session:
            loaded = await session.get(ConnectorConnection, connection.id)
            assert loaded is not None
            with pytest.raises(throttle.ProviderUnavailable):
                await fresh_access_token(session, loaded)
