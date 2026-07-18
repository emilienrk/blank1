# TestClient/httpx exposent des membres partiellement typés.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
"""Flux OAuth tiers des connecteurs (Phase 5 T3).

start → URL avec scopes du manifest et state signé ; callback → tokens chiffrés
en DB tenant (valeur en base ≠ token), webhook_routes créé, audit émis ; state
altéré → 400 ; callback sans refresh token (Google sans prompt=consent) → erreur.
"""

from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.audit.tenant_models import AuditEvent
from app.connectors import service
from app.connectors.models import WebhookRoute
from app.connectors.registry import override_provider
from app.connectors.tenant_models import ConnectorConnection
from app.core.config import Settings, get_settings
from app.core.db import get_control_sessionmaker
from app.main import create_app
from app.tenancy.context import TenantContext, tenant_context
from app.tenancy.provisioning import provision_tenant
from app.tenancy.session import tenant_session
from tests.conftest import requires_postgres
from tests.helpers import add_membership, create_session_token, create_user, reset_db_engines

pytestmark = requires_postgres

BASE = "https://fake.google.test"


def _handler(token_response: dict[str, Any]) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == f"{BASE}/token":
            return httpx.Response(200, json=token_response)
        if url == f"{BASE}/userinfo":
            return httpx.Response(200, json={"email": "contact@acme.test"})
        return httpx.Response(404)

    return handler


def _configure_google(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_CONNECTOR_CLIENT_ID", "google-connector-id")
    monkeypatch.setenv("GOOGLE_CONNECTOR_CLIENT_SECRET", "google-connector-secret")
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://app.example.fr")
    get_settings.cache_clear()


async def _make_owner(tenant_id: object) -> str:
    owner = await create_user("owner@example.com")
    await add_membership(owner.id, tenant_id, "owner")  # type: ignore[arg-type]
    return await create_session_token(owner.id)


def _start(client: TestClient, token: str, settings: Settings) -> tuple[str, str]:
    response = client.get(
        "/api/v1/connectors/google/start",
        headers={"host": "acme.app.example.fr"},
        cookies={settings.session_cookie_name: token},
    )
    assert response.status_code == 200, response.text
    url = response.json()["authorization_url"]
    query = parse_qs(urlsplit(url).query)
    return url, query["state"][0]


async def test_start_returns_authorization_url_with_manifest_scopes(
    db_env: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.connector_helpers import fake_google_manifest

    _configure_google(monkeypatch)
    tenant = await provision_tenant("acme", "ACME")
    token = await _make_owner(tenant.id)
    await reset_db_engines()

    with override_provider(fake_google_manifest()), TestClient(create_app()) as client:
        url, state = _start(client, token, db_env)
        query = parse_qs(urlsplit(url).query)
        assert url.startswith(f"{BASE}/authorize")
        # Scopes du manifest présents (gmail + calendar), refresh garanti chez Google.
        assert "gmail.readonly" in query["scope"][0]
        assert query["access_type"] == ["offline"]
        assert query["prompt"] == ["consent"]
        assert state  # state signé présent


async def test_callback_stores_encrypted_tokens_route_and_audit(
    db_env: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.connector_helpers import fake_google_manifest

    _configure_google(monkeypatch)
    tenant = await provision_tenant("acme", "ACME")
    token = await _make_owner(tenant.id)
    await reset_db_engines()

    token_response = {
        "access_token": "google-access-secret",
        "refresh_token": "google-refresh-secret",
        "expires_in": 3600,
        "scope": " ".join(fake_google_manifest().scopes_for(["mail", "calendar"])),
    }
    transport = httpx.MockTransport(_handler(token_response))
    monkeypatch.setattr(
        service, "_http_client_factory", lambda: httpx.AsyncClient(transport=transport)
    )

    dispatched: list[tuple[str, object]] = []

    async def fake_enqueue(slug: str, connection_id: object) -> None:
        dispatched.append((slug, connection_id))

    import app.connectors.tasks as connector_tasks

    monkeypatch.setattr(connector_tasks, "enqueue_subscription_sync", fake_enqueue)

    with override_provider(fake_google_manifest()), TestClient(create_app()) as client:
        _, state = _start(client, token, db_env)
        callback = client.get(
            f"/api/v1/connectors/google/callback?code=auth-code&state={state}",
            follow_redirects=False,
        )
        assert callback.status_code == 303
        assert "/connectors?connected=google" in callback.headers["location"]

    # La subscription-sync a bien été dispatchée pour ce tenant.
    assert dispatched and dispatched[0][0] == "acme"

    await reset_db_engines()
    # Tokens chiffrés en DB tenant : la valeur brute n'apparaît jamais.
    ctx = TenantContext(tenant_id=tenant.id, slug=tenant.slug)
    with tenant_context(ctx):
        async with tenant_session() as session:
            connection = (await session.scalars(select(ConnectorConnection))).one()
            assert connection.account_label == "contact@acme.test"
            assert connection.access_token_enc != b"google-access-secret"
            assert b"google-access-secret" not in connection.access_token_enc
            assert service.decrypt_token(connection.access_token_enc) == "google-access-secret"
            assert service.decrypt_token(connection.refresh_token_enc) == "google-refresh-secret"
            actions = [e.action for e in (await session.scalars(select(AuditEvent))).all()]
            assert "connector.connected" in actions

    # Route de webhook créée en control-plane (routage seul, D6).
    async with get_control_sessionmaker()() as control_session:
        route = (await control_session.scalars(select(WebhookRoute))).one()
        assert route.tenant_id == tenant.id
        assert route.provider == "google"


async def test_callback_rejects_tampered_state(
    db_env: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.connector_helpers import fake_google_manifest

    _configure_google(monkeypatch)
    tenant = await provision_tenant("acme", "ACME")
    token = await _make_owner(tenant.id)
    await reset_db_engines()

    with override_provider(fake_google_manifest()), TestClient(create_app()) as client:
        _, state = _start(client, token, db_env)
        tampered = state[:-4] + "AAAA"
        callback = client.get(
            f"/api/v1/connectors/google/callback?code=auth-code&state={tampered}",
            follow_redirects=False,
        )
        assert callback.status_code == 400


async def test_callback_without_refresh_token_is_rejected(
    db_env: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.connector_helpers import fake_google_manifest

    _configure_google(monkeypatch)
    tenant = await provision_tenant("acme", "ACME")
    token = await _make_owner(tenant.id)
    await reset_db_engines()

    # Google sans prompt=consent : pas de refresh_token → erreur explicite.
    token_response = {"access_token": "a", "expires_in": 3600, "scope": "openid email"}
    transport = httpx.MockTransport(_handler(token_response))
    monkeypatch.setattr(
        service, "_http_client_factory", lambda: httpx.AsyncClient(transport=transport)
    )

    with override_provider(fake_google_manifest()), TestClient(create_app()) as client:
        _, state = _start(client, token, db_env)
        callback = client.get(
            f"/api/v1/connectors/google/callback?code=auth-code&state={state}",
            follow_redirects=False,
        )
        assert callback.status_code == 400
        assert "refresh token" in callback.json()["detail"].lower()


async def test_start_requires_manage_permission(
    db_env: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.connector_helpers import fake_google_manifest

    _configure_google(monkeypatch)
    tenant = await provision_tenant("acme", "ACME")
    member = await create_user("bob@example.com")
    await add_membership(member.id, tenant.id, "member")
    member_token = await create_session_token(member.id)
    await reset_db_engines()

    with override_provider(fake_google_manifest()), TestClient(create_app()) as client:
        response = client.get(
            "/api/v1/connectors/google/start",
            headers={"host": "acme.app.example.fr"},
            cookies={db_env.session_cookie_name: member_token},
        )
        # Un member peut lire, pas gérer : start exige core.connectors.manage.
        assert response.status_code == 403
