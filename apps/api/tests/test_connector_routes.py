# TestClient/httpx exposent des membres partiellement typés.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
"""Routes de gestion des connecteurs (Phase 5 T3).

Matrice de permissions (read/manage) ; la réponse ne porte jamais de token ;
DELETE → tokens effacés même si la révocation distante échoue (D9) ; reconsent
sur connexion saine → 409.
"""

import uuid

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.audit.tenant_models import AuditEvent
from app.connectors.models import WebhookRoute
from app.connectors.registry import override_provider
from app.connectors.tenant_models import ConnectionStatus, ConnectorProvider
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
    fake_google_manifest,
    install_fake_transport,
    load_connection,
)
from tests.helpers import add_membership, create_session_token, create_user, reset_db_engines

pytestmark = requires_postgres

HOST = {"host": "acme.app.example.fr"}


async def _member(tenant_id: uuid.UUID, email: str, role: str) -> str:
    user = await create_user(email)
    await add_membership(user.id, tenant_id, role)
    return await create_session_token(user.id)


async def test_list_visible_to_all_members_without_tokens(db_env: Settings) -> None:
    tenant = await provision_tenant("acme", "ACME")
    connection = await create_connection(tenant, account_label="contact@acme.test")
    member_token = await _member(tenant.id, "bob@example.com", "member")
    await reset_db_engines()

    with TestClient(create_app()) as client:
        client.cookies.set(db_env.session_cookie_name, member_token)
        response = client.get("/api/v1/connectors", headers=HOST)
        assert response.status_code == 200
        body = response.json()
        assert len(body) == 1
        row = body[0]
        assert row["account_label"] == "contact@acme.test"
        assert str(connection.id) == row["id"]
        # Aucun token, chiffré ou non, dans la réponse.
        serialized = response.text.lower()
        assert "token" not in serialized or "access_token_enc" not in serialized
        assert "access_token_enc" not in row
        assert "refresh_token" not in row


async def test_member_cannot_manage(db_env: Settings) -> None:
    tenant = await provision_tenant("acme", "ACME")
    connection = await create_connection(tenant)
    member_token = await _member(tenant.id, "bob@example.com", "member")
    await reset_db_engines()

    with TestClient(create_app()) as client:
        client.cookies.set(db_env.session_cookie_name, member_token)
        # Révocation réservée à owner/admin (core.connectors.manage).
        revoke = client.delete(f"/api/v1/connectors/{connection.id}", headers=HOST)
        assert revoke.status_code == 403


async def test_delete_erases_tokens_even_if_remote_revoke_fails(
    db_env: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = await provision_tenant("acme", "ACME")
    connection = await create_connection(tenant, provider=ConnectorProvider.GOOGLE)
    admin_token = await _member(tenant.id, "admin@example.com", "admin")
    # Route de webhook préexistante : doit disparaître à la révocation.
    async with get_control_sessionmaker()() as cp:
        cp.add(
            WebhookRoute(
                route_key="rk-1",
                provider="google",
                tenant_id=tenant.id,
                connection_id=connection.id,
            )
        )
        await cp.commit()
    await reset_db_engines()

    def handler(request: httpx.Request) -> httpx.Response:
        # La révocation distante échoue (500) : ne doit PAS bloquer (D9).
        if "/revoke" in str(request.url):
            return httpx.Response(500)
        return httpx.Response(404)

    install_fake_transport(monkeypatch, handler)

    with override_provider(fake_google_manifest()), TestClient(create_app()) as client:
        client.cookies.set(db_env.session_cookie_name, admin_token)
        revoke = client.delete(f"/api/v1/connectors/{connection.id}", headers=HOST)
        assert revoke.status_code == 200

    await reset_db_engines()
    reloaded = await load_connection(tenant, connection.id)
    assert reloaded.status is ConnectionStatus.REVOKED
    # Tokens détruits localement quoi qu'il arrive.
    assert reloaded.access_token_enc == b""
    assert reloaded.refresh_token_enc == b""

    # Audit + suppression de la route de webhook.
    with tenant_context(ctx_for(tenant)):
        async with tenant_session() as session:
            actions = [e.action for e in (await session.scalars(select(AuditEvent))).all()]
            assert "connector.revoked" in actions
    async with get_control_sessionmaker()() as cp:
        assert (await cp.scalars(select(WebhookRoute))).first() is None


async def test_reconsent_on_healthy_connection_returns_409(db_env: Settings) -> None:
    tenant = await provision_tenant("acme", "ACME")
    connection = await create_connection(tenant, status=ConnectionStatus.ACTIVE)
    admin_token = await _member(tenant.id, "admin@example.com", "admin")
    await reset_db_engines()

    with TestClient(create_app()) as client:
        client.cookies.set(db_env.session_cookie_name, admin_token)
        response = client.post(f"/api/v1/connectors/{connection.id}/reconsent", headers=HOST)
        assert response.status_code == 409


async def test_reconsent_on_needs_reconsent_returns_authorization_url(
    db_env: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GOOGLE_CONNECTOR_CLIENT_ID", "id")
    monkeypatch.setenv("GOOGLE_CONNECTOR_CLIENT_SECRET", "secret")
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://app.example.fr")
    from app.core.config import get_settings

    get_settings.cache_clear()
    tenant = await provision_tenant("acme", "ACME")
    connection = await create_connection(tenant, status=ConnectionStatus.NEEDS_RECONSENT)
    admin_token = await _member(tenant.id, "admin@example.com", "admin")
    await reset_db_engines()

    with override_provider(fake_google_manifest()), TestClient(create_app()) as client:
        client.cookies.set(db_env.session_cookie_name, admin_token)
        response = client.post(f"/api/v1/connectors/{connection.id}/reconsent", headers=HOST)
        assert response.status_code == 200
        assert response.json()["authorization_url"].startswith("https://fake.google.test/authorize")
