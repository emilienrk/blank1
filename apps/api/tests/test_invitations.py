# TestClient (starlette/httpx) expose des membres partiellement typés.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
"""Invitations (Phase 2 T8) : cycle complet, expiration, usage unique, décision D8."""

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth.models import Invitation
from app.core.config import Settings
from app.core.db import get_control_sessionmaker
from app.main import create_app
from app.tenancy.provisioning import provision_tenant
from tests.conftest import requires_postgres
from tests.helpers import (
    add_membership,
    create_session_token,
    create_user,
    reset_db_engines,
)

pytestmark = requires_postgres

PASSWORD = "un-mot-de-passe-solide"


def _token_from_accept_url(accept_url: str) -> str:
    return parse_qs(urlsplit(accept_url).query)["token"][0]


async def test_invitation_full_cycle_new_user(db_env: Settings) -> None:
    tenant = await provision_tenant("acme", "ACME")
    admin = await create_user("admin@example.com")
    await add_membership(admin.id, tenant.id, "admin")
    admin_token = await create_session_token(admin.id)
    await reset_db_engines()

    host = {"host": "acme.app.example.fr"}
    with TestClient(create_app()) as client:
        client.cookies.set(db_env.session_cookie_name, admin_token)
        created = client.post(
            "/api/v1/directory/invitations",
            headers=host,
            json={"email": "bob@example.com", "role": "member"},
        )
        assert created.status_code == 201
        # Décision D8 : l'URL d'acceptation est TOUJOURS retournée à l'appelant.
        accept_url = created.json()["accept_url"]
        token = _token_from_accept_url(accept_url)

        client.cookies.delete(db_env.session_cookie_name)
        accepted = client.post(
            "/api/v1/auth/invitations/accept",
            json={"token": token, "password": PASSWORD, "display_name": "Bob"},
        )
        assert accepted.status_code == 200

        # Le nouveau compte se connecte et voit l'annuaire du tenant.
        login = client.post(
            "/api/v1/auth/login", json={"email": "bob@example.com", "password": PASSWORD}
        )
        assert login.status_code == 200
        members = client.get("/api/v1/directory/members", headers=host)
        assert members.status_code == 200
        emails = {member["email"] for member in members.json()}
        assert emails == {"admin@example.com", "bob@example.com"}

        # Usage unique : le token consommé est refusé.
        replay = client.post(
            "/api/v1/auth/invitations/accept", json={"token": token, "password": PASSWORD}
        )
        assert replay.status_code == 400


async def test_invitation_existing_user_joins_second_tenant(db_env: Settings) -> None:
    acme = await provision_tenant("acme", "ACME")
    globex = await provision_tenant("globex", "Globex")
    owner = await create_user("owner@example.com")
    await add_membership(owner.id, globex.id, "owner")
    owner_token = await create_session_token(owner.id)
    bob = await create_user("bob@example.com", PASSWORD)
    await add_membership(bob.id, acme.id, "member")
    bob_token = await create_session_token(bob.id)
    await reset_db_engines()

    with TestClient(create_app()) as client:
        client.cookies.set(db_env.session_cookie_name, owner_token)
        created = client.post(
            "/api/v1/directory/invitations",
            headers={"host": "globex.app.example.fr"},
            json={"email": "bob@example.com", "role": "admin"},
        )
        assert created.status_code == 201
        token = _token_from_accept_url(created.json()["accept_url"])

        # Un compte existant n'a pas à fournir de mot de passe…
        with_password = client.post(
            "/api/v1/auth/invitations/accept", json={"token": token, "password": PASSWORD}
        )
        assert with_password.status_code == 400

        accepted = client.post("/api/v1/auth/invitations/accept", json={"token": token})
        assert accepted.status_code == 200

        client.cookies.set(db_env.session_cookie_name, bob_token)
        me = client.get("/api/v1/auth/me")
        memberships = {
            (membership["tenant_slug"], membership["role"])
            for membership in me.json()["memberships"]
        }
        assert memberships == {("acme", "member"), ("globex", "admin")}


async def test_expired_and_unknown_invitations_rejected(db_env: Settings) -> None:
    tenant = await provision_tenant("acme", "ACME")
    admin = await create_user("admin@example.com")
    await add_membership(admin.id, tenant.id, "admin")
    admin_token = await create_session_token(admin.id)
    await reset_db_engines()

    host = {"host": "acme.app.example.fr"}
    with TestClient(create_app()) as client:
        client.cookies.set(db_env.session_cookie_name, admin_token)
        created = client.post(
            "/api/v1/directory/invitations",
            headers=host,
            json={"email": "late@example.com", "role": "member"},
        )
        token = _token_from_accept_url(created.json()["accept_url"])

    # Expiration forcée en base.
    await reset_db_engines()
    async with get_control_sessionmaker()() as session:
        invitation = await session.scalar(select(Invitation))
        assert invitation is not None
        invitation.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        await session.commit()
    await reset_db_engines()

    with TestClient(create_app()) as client:
        expired = client.post(
            "/api/v1/auth/invitations/accept", json={"token": token, "password": PASSWORD}
        )
        assert expired.status_code == 400
        unknown = client.post("/api/v1/auth/invitations/accept", json={"token": "token-inconnu"})
        assert unknown.status_code == 400


async def test_duplicate_pending_invitation_rejected_and_revocable(db_env: Settings) -> None:
    tenant = await provision_tenant("acme", "ACME")
    admin = await create_user("admin@example.com")
    await add_membership(admin.id, tenant.id, "admin")
    admin_token = await create_session_token(admin.id)
    await reset_db_engines()

    host = {"host": "acme.app.example.fr"}
    with TestClient(create_app()) as client:
        client.cookies.set(db_env.session_cookie_name, admin_token)
        first = client.post(
            "/api/v1/directory/invitations",
            headers=host,
            json={"email": "bob@example.com", "role": "member"},
        )
        assert first.status_code == 201
        duplicate = client.post(
            "/api/v1/directory/invitations",
            headers=host,
            json={"email": "bob@example.com", "role": "member"},
        )
        assert duplicate.status_code == 400

        revoked = client.delete(f"/api/v1/directory/invitations/{first.json()['id']}", headers=host)
        assert revoked.status_code == 200
        again = client.post(
            "/api/v1/directory/invitations",
            headers=host,
            json={"email": "bob@example.com", "role": "member"},
        )
        assert again.status_code == 201


async def test_list_pending_invitations(db_env: Settings) -> None:
    tenant = await provision_tenant("acme", "ACME")
    admin = await create_user("admin@example.com")
    await add_membership(admin.id, tenant.id, "admin")
    admin_token = await create_session_token(admin.id)
    await reset_db_engines()

    host = {"host": "acme.app.example.fr"}
    with TestClient(create_app()) as client:
        client.cookies.set(db_env.session_cookie_name, admin_token)
        created = client.post(
            "/api/v1/directory/invitations",
            headers=host,
            json={"email": "bob@example.com", "role": "member"},
        )
        assert created.status_code == 201
        invitation_id = created.json()["id"]

        listed = client.get("/api/v1/directory/invitations", headers=host)
        assert listed.status_code == 200
        rows = listed.json()
        assert len(rows) == 1
        assert rows[0]["id"] == invitation_id
        assert rows[0]["email"] == "bob@example.com"
        # Le token n'apparaît jamais dans le listing (invariant n°5).
        assert "accept_url" not in rows[0]
        assert "token" not in rows[0]

        client.delete(f"/api/v1/directory/invitations/{invitation_id}", headers=host)
        listed_after = client.get("/api/v1/directory/invitations", headers=host)
        assert listed_after.json() == []


async def test_inviting_existing_member_rejected(db_env: Settings) -> None:
    tenant = await provision_tenant("acme", "ACME")
    admin = await create_user("admin@example.com")
    await add_membership(admin.id, tenant.id, "admin")
    admin_token = await create_session_token(admin.id)
    await reset_db_engines()

    with TestClient(create_app()) as client:
        client.cookies.set(db_env.session_cookie_name, admin_token)
        response = client.post(
            "/api/v1/directory/invitations",
            headers={"host": "acme.app.example.fr"},
            json={"email": "ADMIN@example.com", "role": "member"},  # casse différente
        )
        assert response.status_code == 400
        assert "déjà membre" in response.json()["detail"]
