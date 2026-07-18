# TestClient (starlette/httpx) expose des membres partiellement typés.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
"""RBAC (Phase 2 T7) : matrice rôle x permission, dépendance unique."""

from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.auth.permissions import (
    PERMISSIONS,
    ROLE_ADMIN,
    ROLE_MEMBER,
    ROLE_OWNER,
    require_permission,
    role_has_permission,
    validate_role,
)
from app.core.config import Settings
from app.main import create_app
from app.tenancy.provisioning import provision_tenant
from tests.conftest import requires_postgres
from tests.helpers import (
    add_catalog_tenant,
    add_membership,
    create_session_token,
    create_user,
    reset_db_engines,
)


@pytest.mark.parametrize(
    ("role", "permission", "expected"),
    [
        (ROLE_MEMBER, "core.members.read", True),
        (ROLE_MEMBER, "core.teams.read", True),
        (ROLE_MEMBER, "core.members.manage", False),
        (ROLE_MEMBER, "core.teams.manage", False),
        (ROLE_MEMBER, "core.tenant.settings", False),
        (ROLE_ADMIN, "core.members.manage", True),
        (ROLE_ADMIN, "core.teams.manage", True),
        (ROLE_ADMIN, "core.tenant.settings", True),
        (ROLE_OWNER, "core.members.manage", True),
        (ROLE_OWNER, "core.tenant.settings", True),
        ("role-inconnu", "core.members.read", False),
    ],
)
def test_role_permission_matrix(role: str, permission: str, expected: bool) -> None:
    assert permission in PERMISSIONS or not expected
    assert role_has_permission(role, permission) is expected


def test_validate_role_rejects_unknown() -> None:
    validate_role("owner")
    with pytest.raises(ValueError, match="Rôle inconnu"):
        validate_role("superuser")


def test_require_permission_rejects_unknown_permission() -> None:
    with pytest.raises(ValueError, match="Permission inconnue"):
        require_permission("core.nexiste.pas")


@requires_postgres
async def test_permission_matrix_over_http(db_env: Settings) -> None:
    """member lit l'annuaire mais ne gère pas ; l'anonyme est 401 ; l'étranger 403."""
    tenant = await provision_tenant("acme", "ACME")
    member = await create_user("member@example.com")
    await add_membership(member.id, tenant.id, ROLE_MEMBER)
    member_token = await create_session_token(member.id)
    admin = await create_user("admin@example.com")
    await add_membership(admin.id, tenant.id, ROLE_ADMIN)
    admin_token = await create_session_token(admin.id)
    await reset_db_engines()

    host = {"host": "acme.app.example.fr"}
    with TestClient(create_app()) as client:
        assert client.get("/api/v1/directory/members", headers=host).status_code == 401

        client.cookies.set(db_env.session_cookie_name, member_token)
        assert client.get("/api/v1/directory/members", headers=host).status_code == 200
        forbidden = client.post(
            "/api/v1/directory/invitations",
            headers=host,
            json={"email": "x@example.com", "role": "member"},
        )
        assert forbidden.status_code == 403

        client.cookies.set(db_env.session_cookie_name, admin_token)
        allowed = client.post(
            "/api/v1/directory/invitations",
            headers=host,
            json={"email": "x@example.com", "role": "member"},
        )
        assert allowed.status_code == 201

        # admin ne peut PAS inviter un owner (règle métier au-delà du RBAC).
        owner_invite = client.post(
            "/api/v1/directory/invitations",
            headers=host,
            json={"email": "boss@example.com", "role": "owner"},
        )
        assert owner_invite.status_code == 400


@requires_postgres
async def test_last_owner_is_untouchable(db_env: Settings) -> None:
    tenant = await add_catalog_tenant("acme")
    owner = await create_user("owner@example.com")
    await add_membership(owner.id, tenant.id, ROLE_OWNER)
    owner_token = await create_session_token(owner.id)
    await reset_db_engines()

    host = {"host": "acme.app.example.fr"}
    with TestClient(create_app()) as client:
        client.cookies.set(db_env.session_cookie_name, owner_token)
        demote = client.patch(
            f"/api/v1/directory/members/{owner.id}", headers=host, json={"role": "admin"}
        )
        assert demote.status_code == 400
        assert "dernier owner" in demote.json()["detail"]

        remove = client.delete(f"/api/v1/directory/members/{owner.id}", headers=host)
        assert remove.status_code == 400
        assert "dernier owner" in remove.json()["detail"]


@requires_postgres
async def test_admin_cannot_touch_owner_membership(db_env: Settings) -> None:
    tenant = await add_catalog_tenant("acme")
    owner = await create_user("owner@example.com")
    await add_membership(owner.id, tenant.id, ROLE_OWNER)
    admin = await create_user("admin@example.com")
    await add_membership(admin.id, tenant.id, ROLE_ADMIN)
    admin_token = await create_session_token(admin.id)
    await reset_db_engines()

    host = {"host": "acme.app.example.fr"}
    with TestClient(create_app()) as client:
        client.cookies.set(db_env.session_cookie_name, admin_token)
        demote = client.patch(
            f"/api/v1/directory/members/{owner.id}", headers=host, json={"role": "member"}
        )
        assert demote.status_code == 400
        remove = client.delete(f"/api/v1/directory/members/{owner.id}", headers=host)
        assert remove.status_code == 400


def test_permission_app_smoke() -> None:
    """require_permission se compose sans erreur à la déclaration de route."""
    app = FastAPI()

    @app.get("/x")
    async def x(  # pyright: ignore[reportUnusedFunction]
        ctx: Annotated[object, Depends(require_permission("core.members.read"))],
    ) -> dict[str, str]:
        return {}

    assert app is not None
