# TestClient/httpx exposent des membres partiellement typés.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
"""Montage des modules (Phase 7 T2).

Routes montées sous `/api/v1/modules/<name>/` ; module non activé → 403 même avec la
permission ; activé → matrice des permissions du module ; handlers connecteurs
enregistrés au montage."""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.connectors.registry import CAPABILITY_MAIL
from app.connectors.webhooks import event_handlers
from app.core.config import Settings
from app.main import create_app
from app.tenancy.provisioning import provision_tenant
from tests.conftest import requires_postgres
from tests.helpers import add_membership, create_session_token, create_user, reset_db_engines
from tests.module_helpers import enable_module_row

pytestmark = requires_postgres

HOST = {"host": "acme.app.example.fr"}


async def _member(tenant_id: uuid.UUID, email: str, role: str) -> str:
    user = await create_user(email)
    await add_membership(user.id, tenant_id, role)
    return await create_session_token(user.id)


def test_connector_handlers_registered_at_mount() -> None:
    # `create_app` monte les modules : le handler mail de sample_digest est branché
    # sur le hook connecteur (Phase 5 D7) — premier client réel du hook.
    create_app()
    handlers = event_handlers(CAPABILITY_MAIL)
    assert any(getattr(h, "__name__", "") == "on_mail_event" for h in handlers)


async def test_route_403_when_module_disabled_even_with_permission(db_env: Settings) -> None:
    tenant = await provision_tenant("acme", "ACME")
    owner_token = await _member(tenant.id, "owner@example.com", "owner")
    await reset_db_engines()

    with TestClient(create_app()) as client:
        client.cookies.set(db_env.session_cookie_name, owner_token)
        # L'owner a la permission, mais le module n'est pas activé → 403 explicite.
        response = client.get("/api/v1/modules/sample_digest/digests", headers=HOST)
        assert response.status_code == 403
        assert "sample_digest" in response.json()["detail"]


async def test_permission_matrix_when_enabled(
    db_env: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = await provision_tenant("acme", "ACME")
    member_token = await _member(tenant.id, "bob@example.com", "member")
    admin_token = await _member(tenant.id, "alice@example.com", "admin")
    await enable_module_row(tenant, "sample_digest")
    await reset_db_engines()

    dispatched: list[tuple[str, str, str]] = []

    async def _fake_enqueue(module: str, task: str, tenant_id: uuid.UUID) -> None:
        dispatched.append((module, task, str(tenant_id)))

    from app.automation import scheduler

    monkeypatch.setattr(scheduler, "enqueue_unit", _fake_enqueue)

    with TestClient(create_app()) as client:
        # Lecture : tous les rôles (sample_digest.read).
        client.cookies.set(db_env.session_cookie_name, member_token)
        assert client.get("/api/v1/modules/sample_digest/digests", headers=HOST).status_code == 200
        # Déclenchement manuel : owner/admin uniquement (sample_digest.manage).
        assert client.post("/api/v1/modules/sample_digest/run", headers=HOST).status_code == 403

        client.cookies.set(db_env.session_cookie_name, admin_token)
        run = client.post("/api/v1/modules/sample_digest/run", headers=HOST)
        assert run.status_code == 202
        assert run.json()["status"] == "scheduled"

    assert dispatched == [("sample_digest", "sample_digest.daily_digest", str(tenant.id))]
