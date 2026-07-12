# TestClient (starlette/httpx) expose des membres partiellement typés.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
"""Back-office (Phase 3 T6) : `/api/v1/admin/*`, toutes derrière `require_platform_admin`."""

import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.admin import service as admin_service
from app.admin import tasks as admin_tasks
from app.core.config import Settings
from app.core.db import get_control_sessionmaker
from app.directory.models import Membership, User
from app.gdpr import export as gdpr_export
from app.gdpr import tasks as gdpr_tasks
from app.main import create_app
from app.tenancy.models import Tenant, TenantState
from app.tenancy.provisioning import create_database, provision_tenant
from tests.conftest import TENANT_HEAD_REVISION, requires_postgres
from tests.helpers import add_catalog_tenant, create_session_token, create_user, reset_db_engines

pytestmark = requires_postgres


async def _promote(user: User) -> None:
    async with get_control_sessionmaker()() as session:
        stored = await session.get(User, user.id)
        assert stored is not None
        stored.is_platform_admin = True
        await session.commit()


def _token_from_accept_url(accept_url: str) -> str:
    return parse_qs(urlsplit(accept_url).query)["token"][0]


async def test_tenants_list_requires_platform_admin(db_env: Settings) -> None:
    await add_catalog_tenant("acme")
    regular = await create_user("user@example.com")
    regular_token = await create_session_token(regular.id)
    admin = await create_user("root@example.com")
    await _promote(admin)
    admin_token = await create_session_token(admin.id)
    await reset_db_engines()

    with TestClient(create_app()) as client:
        assert client.get("/api/v1/admin/tenants").status_code == 401

        client.cookies.set(db_env.session_cookie_name, regular_token)
        assert client.get("/api/v1/admin/tenants").status_code == 403

        client.cookies.set(db_env.session_cookie_name, admin_token)
        response = client.get("/api/v1/admin/tenants")
        assert response.status_code == 200
        slugs = {row["slug"] for row in response.json()}
        assert slugs == {"acme"}


async def test_create_tenant_via_api_matches_cli_effects(db_env: Settings) -> None:
    admin = await create_user("root@example.com")
    await _promote(admin)
    admin_token = await create_session_token(admin.id)
    await reset_db_engines()

    with TestClient(create_app()) as client:
        client.cookies.set(db_env.session_cookie_name, admin_token)
        created = client.post(
            "/api/v1/admin/tenants",
            json={"slug": "globex", "name": "Globex", "owner_email": "owner@example.com"},
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["tenant"]["state"] == "active"
        assert body["tenant"]["schema_revision"] == TENANT_HEAD_REVISION
        assert body["owner_invitation_accept_url"] is not None

        # L'invitation owner fonctionne comme celle du CLI (même chemin de service).
        token = _token_from_accept_url(body["owner_invitation_accept_url"])
        client.cookies.delete(db_env.session_cookie_name)
        accepted = client.post(
            "/api/v1/auth/invitations/accept",
            json={"token": token, "password": "un-mot-de-passe-solide"},
        )
        assert accepted.status_code == 200


async def test_create_tenant_duplicate_slug_rejected(db_env: Settings) -> None:
    await add_catalog_tenant("acme")
    admin = await create_user("root@example.com")
    await _promote(admin)
    admin_token = await create_session_token(admin.id)
    await reset_db_engines()

    with TestClient(create_app()) as client:
        client.cookies.set(db_env.session_cookie_name, admin_token)
        response = client.post("/api/v1/admin/tenants", json={"slug": "acme"})
        assert response.status_code == 400


async def test_retry_provision_over_http(db_env: Settings) -> None:
    db_name = f"{db_env.tenant_db_prefix}acme"
    await create_database(db_name, db_env)  # sabote le provisioning (base déjà là)
    admin = await create_user("root@example.com")
    await _promote(admin)
    admin_token = await create_session_token(admin.id)
    await reset_db_engines()

    with TestClient(create_app()) as client:
        client.cookies.set(db_env.session_cookie_name, admin_token)
        failed = client.post("/api/v1/admin/tenants", json={"slug": "acme"})
        assert failed.status_code == 400

    await reset_db_engines()
    async with get_control_sessionmaker()() as session:
        tenant = await session.scalar(select(Tenant).where(Tenant.slug == "acme"))
        assert tenant is not None
        assert tenant.state is TenantState.FAILED
    await reset_db_engines()

    with TestClient(create_app()) as client:
        client.cookies.set(db_env.session_cookie_name, admin_token)
        retried = client.post("/api/v1/admin/tenants/acme/retry-provision")
        assert retried.status_code == 200, retried.text
        assert retried.json()["state"] == "active"


async def test_lookup_user(db_env: Settings) -> None:
    tenant = await add_catalog_tenant("acme")
    admin = await create_user("root@example.com")
    await _promote(admin)
    admin_token = await create_session_token(admin.id)
    bob = await create_user("bob@example.com")
    async with get_control_sessionmaker()() as session:
        session.add(Membership(user_id=bob.id, tenant_id=tenant.id, role="member"))
        await session.commit()
    await reset_db_engines()

    with TestClient(create_app()) as client:
        client.cookies.set(db_env.session_cookie_name, admin_token)
        found = client.get("/api/v1/admin/users/bob@example.com")
        assert found.status_code == 200
        assert found.json()["memberships"] == [{"tenant_slug": "acme", "role": "member"}]

        missing = client.get("/api/v1/admin/users/ghost@example.com")
        assert missing.status_code == 404


async def test_run_migrations_persists_report_and_polls(
    db_env: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Décision D6 : la route ne fait que déclencher — l'exécution simule le worker
    Celery (même fonction que la tâche, sans dépendre d'un broker en test)."""
    await provision_tenant("acme", "ACME")
    admin = await create_user("root@example.com")
    await _promote(admin)
    admin_token = await create_session_token(admin.id)
    await reset_db_engines()

    executed: list[str] = []

    async def fake_enqueue(report_id: uuid.UUID) -> None:
        executed.append(str(report_id))
        await admin_service.execute_migration_report(report_id)

    monkeypatch.setattr(admin_tasks, "enqueue_migration_run", fake_enqueue)

    with TestClient(create_app()) as client:
        client.cookies.set(db_env.session_cookie_name, admin_token)
        started = client.post("/api/v1/admin/migrations/run")
        assert started.status_code == 202, started.text
        assert started.json()["status"] == "running"
        assert executed  # la tâche a bien été « dispatchée »

        last = client.get("/api/v1/admin/migrations/last-report")
        assert last.status_code == 200
        report = last.json()
        assert report["status"] == "done"
        assert report["summary"] is not None
        by_target = {o["target"]: o for o in report["outcomes"]}
        assert set(by_target) == {"controlplane", "acme"}
        assert all(o["ok"] for o in by_target.values())


async def test_last_report_empty_when_never_run(db_env: Settings) -> None:
    admin = await create_user("root@example.com")
    await _promote(admin)
    admin_token = await create_session_token(admin.id)
    await reset_db_engines()

    with TestClient(create_app()) as client:
        client.cookies.set(db_env.session_cookie_name, admin_token)
        response = client.get("/api/v1/admin/migrations/last-report")
        assert response.status_code == 200
        assert response.json() is None


async def test_admin_export_dispatches_and_lists_download(
    db_env: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_env.gdpr_export_dir = str(tmp_path)
    await provision_tenant("acme", "ACME")
    admin = await create_user("root@example.com")
    await _promote(admin)
    admin_token = await create_session_token(admin.id)
    await reset_db_engines()

    dispatched: list[str] = []

    async def fake_enqueue(slug: str) -> None:
        dispatched.append(slug)
        await gdpr_export.run_export(slug, settings=db_env)

    monkeypatch.setattr(gdpr_tasks, "enqueue_export", fake_enqueue)

    with TestClient(create_app()) as client:
        client.cookies.set(db_env.session_cookie_name, admin_token)
        started = client.post("/api/v1/admin/tenants/acme/export")
        assert started.status_code == 202, started.text
        assert dispatched == ["acme"]

        listed = client.get("/api/v1/admin/tenants/acme/exports")
        assert listed.status_code == 200
        files = listed.json()
        assert len(files) == 1
        filename = files[0]["filename"]

        downloaded = client.get(f"/api/v1/admin/tenants/acme/exports/{filename}/download")
        assert downloaded.status_code == 200
        assert downloaded.content  # archive chiffrée non vide

        missing = client.get("/api/v1/admin/tenants/acme/exports/../../etc/passwd/download")
        assert missing.status_code == 404


async def test_admin_request_and_cancel_erasure(db_env: Settings) -> None:
    await provision_tenant("acme", "ACME")
    admin = await create_user("root@example.com")
    await _promote(admin)
    admin_token = await create_session_token(admin.id)
    await reset_db_engines()

    with TestClient(create_app()) as client:
        client.cookies.set(db_env.session_cookie_name, admin_token)
        requested = client.post("/api/v1/admin/tenants/acme/request-erasure")
        assert requested.status_code == 200, requested.text
        assert requested.json()["state"] == "pending_deletion"

        again = client.post("/api/v1/admin/tenants/acme/request-erasure")
        assert again.status_code == 400

        cancelled = client.post("/api/v1/admin/tenants/acme/cancel-erasure")
        assert cancelled.status_code == 200
        assert cancelled.json()["state"] == "active"

        cancel_again = client.post("/api/v1/admin/tenants/acme/cancel-erasure")
        assert cancel_again.status_code == 400
