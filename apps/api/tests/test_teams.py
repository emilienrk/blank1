# TestClient (starlette/httpx) expose des membres partiellement typés.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
"""Équipes en DB tenant (Phase 2 T8) : première vraie route métier qui traverse
resolve_tenant → get_tenant_session sur une base tenant réelle."""

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.tenancy.provisioning import provision_tenant
from tests.conftest import requires_postgres
from tests.helpers import add_membership, create_session_token, create_user, reset_db_engines

pytestmark = requires_postgres


async def test_teams_crud_through_tenant_db(db_env: Settings) -> None:
    tenant = await provision_tenant("acme", "ACME")  # vraie base tenant migrée
    admin = await create_user("admin@example.com")
    await add_membership(admin.id, tenant.id, "admin")
    admin_token = await create_session_token(admin.id)
    member = await create_user("bob@example.com")
    await add_membership(member.id, tenant.id, "member")
    member_token = await create_session_token(member.id)
    outsider = await create_user("outsider@example.com")
    await reset_db_engines()

    host = {"host": "acme.app.example.fr"}
    with TestClient(create_app()) as client:
        client.cookies.set(db_env.session_cookie_name, admin_token)

        created = client.post(
            "/api/v1/directory/teams", headers=host, json={"name": "Support", "description": "N1"}
        )
        assert created.status_code == 201
        team_id = created.json()["id"]

        duplicate = client.post("/api/v1/directory/teams", headers=host, json={"name": "Support"})
        assert duplicate.status_code == 409

        # Ajout d'un membre du tenant : OK ; d'un non-membre : refus (cohérence inter-bases).
        added = client.post(
            f"/api/v1/directory/teams/{team_id}/members",
            headers=host,
            json={"user_id": str(member.id)},
        )
        assert added.status_code == 201
        rejected = client.post(
            f"/api/v1/directory/teams/{team_id}/members",
            headers=host,
            json={"user_id": str(outsider.id)},
        )
        assert rejected.status_code == 400
        again = client.post(
            f"/api/v1/directory/teams/{team_id}/members",
            headers=host,
            json={"user_id": str(member.id)},
        )
        assert again.status_code == 409

        # Un member lit les équipes mais ne les gère pas (RBAC).
        client.cookies.set(db_env.session_cookie_name, member_token)
        listed = client.get("/api/v1/directory/teams", headers=host)
        assert listed.status_code == 200
        assert [team["name"] for team in listed.json()] == ["Support"]
        forbidden = client.post("/api/v1/directory/teams", headers=host, json={"name": "Autre"})
        assert forbidden.status_code == 403

        client.cookies.set(db_env.session_cookie_name, admin_token)
        removed = client.delete(
            f"/api/v1/directory/teams/{team_id}/members/{member.id}", headers=host
        )
        assert removed.status_code == 200
        deleted = client.delete(f"/api/v1/directory/teams/{team_id}", headers=host)
        assert deleted.status_code == 200
        assert client.get("/api/v1/directory/teams", headers=host).json() == []
