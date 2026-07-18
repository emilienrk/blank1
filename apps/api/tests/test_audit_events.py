# TestClient (starlette/httpx) expose des membres partiellement typés.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
"""Journal d'audit (Phase 4 T1-T3) : émission dans la même transaction que
l'action auditée, consultation paginée par curseur, RBAC (owner/admin only)."""

from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.audit.tenant_models import AuditEvent
from app.core.config import Settings
from app.main import create_app
from app.tenancy.context import TenantContext, tenant_context
from app.tenancy.provisioning import provision_tenant
from app.tenancy.session import get_tenant_session
from tests.conftest import requires_postgres
from tests.helpers import add_membership, create_session_token, create_user, reset_db_engines

pytestmark = requires_postgres


def _token_from_accept_url(accept_url: str) -> str:
    return parse_qs(urlsplit(accept_url).query)["token"][0]


async def _audit_actions(tenant: object) -> list[str]:
    ctx = TenantContext(
        tenant_id=tenant.id,  # type: ignore[attr-defined]
        slug=tenant.slug,  # type: ignore[attr-defined]
    )
    with tenant_context(ctx):
        async for session in get_tenant_session():
            rows = await session.scalars(select(AuditEvent).order_by(AuditEvent.occurred_at))
            return [row.action for row in rows.all()]
    return []  # pragma: no cover — get_tenant_session produit toujours une itération


async def test_provisioning_writes_first_audit_event(db_env: Settings) -> None:
    tenant = await provision_tenant("acme", "ACME")
    actions = await _audit_actions(tenant)
    assert actions == ["core.tenant.provisioned"]


async def test_directory_actions_write_audit_events_through_http(db_env: Settings) -> None:
    tenant = await provision_tenant("acme", "ACME")
    owner = await create_user("owner@example.com")
    await add_membership(owner.id, tenant.id, "owner")
    owner_token = await create_session_token(owner.id)
    member = await create_user("bob@example.com")
    await add_membership(member.id, tenant.id, "member")
    await reset_db_engines()

    host = {"host": "acme.app.example.fr"}
    with TestClient(create_app()) as client:
        client.cookies.set(db_env.session_cookie_name, owner_token)

        invited = client.post(
            "/api/v1/directory/invitations",
            headers=host,
            json={"email": "carol@example.com", "role": "member"},
        )
        assert invited.status_code == 201, invited.text
        accept_url = invited.json()["accept_url"]
        invitation_id = invited.json()["id"]

        revoked_invite = client.post(
            "/api/v1/directory/invitations",
            headers=host,
            json={"email": "dave@example.com", "role": "member"},
        )
        assert revoked_invite.status_code == 201
        revoke_response = client.delete(
            f"/api/v1/directory/invitations/{revoked_invite.json()['id']}", headers=host
        )
        assert revoke_response.status_code == 200

        role_change = client.patch(
            f"/api/v1/directory/members/{member.id}", headers=host, json={"role": "admin"}
        )
        assert role_change.status_code == 200

        team = client.post("/api/v1/directory/teams", headers=host, json={"name": "Support"})
        assert team.status_code == 201
        team_id = team.json()["id"]

        add_team_member = client.post(
            f"/api/v1/directory/teams/{team_id}/members",
            headers=host,
            json={"user_id": str(member.id)},
        )
        assert add_team_member.status_code == 201
        remove_team_member = client.delete(
            f"/api/v1/directory/teams/{team_id}/members/{member.id}", headers=host
        )
        assert remove_team_member.status_code == 200
        team_delete = client.delete(f"/api/v1/directory/teams/{team_id}", headers=host)
        assert team_delete.status_code == 200

        removed = client.delete(f"/api/v1/directory/members/{member.id}", headers=host)
        assert removed.status_code == 200

    # Invitation acceptée depuis la route publique (hors sous-domaine tenant).
    token = _token_from_accept_url(accept_url)
    await reset_db_engines()  # bascule entre deux TestClient = deux event loops
    with TestClient(create_app()) as client:
        accepted = client.post(
            "/api/v1/auth/invitations/accept",
            json={"token": token, "password": "un-mot-de-passe-solide"},
        )
        assert accepted.status_code == 200

    await reset_db_engines()
    actions = await _audit_actions(tenant)
    assert actions == [
        "core.tenant.provisioned",
        "core.member.invited",
        "core.member.invited",
        "core.member.invitation_revoked",
        "core.member.role_changed",
        "core.team.created",
        "core.team.member_added",
        "core.team.member_removed",
        "core.team.deleted",
        "core.member.removed",
        "core.member.invitation_accepted",
    ]
    assert invitation_id  # utilisée uniquement pour lisibilité du scénario


async def test_audit_read_requires_owner_or_admin(db_env: Settings) -> None:
    tenant = await provision_tenant("acme", "ACME")
    admin = await create_user("admin@example.com")
    await add_membership(admin.id, tenant.id, "admin")
    admin_token = await create_session_token(admin.id)
    member = await create_user("bob@example.com")
    await add_membership(member.id, tenant.id, "member")
    member_token = await create_session_token(member.id)
    await reset_db_engines()

    host = {"host": "acme.app.example.fr"}
    with TestClient(create_app()) as client:
        client.cookies.set(db_env.session_cookie_name, member_token)
        forbidden = client.get("/api/v1/audit/events", headers=host)
        assert forbidden.status_code == 403

        client.cookies.set(db_env.session_cookie_name, admin_token)
        allowed = client.get("/api/v1/audit/events", headers=host)
        assert allowed.status_code == 200
        body = allowed.json()
        assert body["items"][0]["action"] == "core.tenant.provisioned"
        assert body["next_cursor"] is None


async def test_audit_pagination_cursor_is_stable(db_env: Settings) -> None:
    tenant = await provision_tenant("acme", "ACME")
    owner = await create_user("owner@example.com")
    await add_membership(owner.id, tenant.id, "owner")
    owner_token = await create_session_token(owner.id)
    await reset_db_engines()

    host = {"host": "acme.app.example.fr"}
    with TestClient(create_app()) as client:
        client.cookies.set(db_env.session_cookie_name, owner_token)
        for index in range(3):
            response = client.post(
                "/api/v1/directory/teams", headers=host, json={"name": f"Team {index}"}
            )
            assert response.status_code == 201

        first_page = client.get("/api/v1/audit/events", headers=host, params={"limit": 2})
        assert first_page.status_code == 200
        first_body = first_page.json()
        assert len(first_body["items"]) == 2
        assert first_body["next_cursor"] is not None

        second_page = client.get(
            "/api/v1/audit/events",
            headers=host,
            params={"limit": 2, "cursor": first_body["next_cursor"]},
        )
        assert second_page.status_code == 200
        second_body = second_page.json()
        first_ids = {item["id"] for item in first_body["items"]}
        second_ids = {item["id"] for item in second_body["items"]}
        assert first_ids.isdisjoint(second_ids)


async def test_failed_role_change_writes_no_audit_event(db_env: Settings) -> None:
    """Preuve indirecte de la décision D1 : une action refusée (400) ne laisse
    aucune trace — l'audit et l'action commitent ensemble ou pas du tout."""
    tenant = await provision_tenant("acme", "ACME")
    owner = await create_user("owner@example.com")
    await add_membership(owner.id, tenant.id, "owner")
    owner_token = await create_session_token(owner.id)
    await reset_db_engines()

    host = {"host": "acme.app.example.fr"}
    with TestClient(create_app()) as client:
        client.cookies.set(db_env.session_cookie_name, owner_token)
        rejected = client.patch(
            f"/api/v1/directory/members/{owner.id}", headers=host, json={"role": "member"}
        )
        assert rejected.status_code == 400  # dernier owner intouchable

    await reset_db_engines()
    actions = await _audit_actions(tenant)
    assert actions == ["core.tenant.provisioned"]
