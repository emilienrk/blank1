# TestClient (starlette/httpx) expose des membres partiellement typés.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
import uuid
from datetime import UTC, datetime
from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import Settings
from app.core.db import get_control_sessionmaker
from app.tenancy.context import (
    TenantContext,
    TenantContextError,
    current_tenant,
    current_tenant_or_none,
    tenant_context,
)
from app.tenancy.deps import extract_slug, resolve_tenant
from app.tenancy.models import Tenant, TenantState
from app.tenancy.session import get_tenant_session
from tests.conftest import requires_postgres
from tests.helpers import add_membership, create_session_token, create_user


def _ctx(slug: str = "acme") -> TenantContext:
    return TenantContext(tenant_id=uuid.uuid4(), slug=slug)


def test_current_tenant_without_context_raises() -> None:
    with pytest.raises(TenantContextError):
        current_tenant()


def test_tenant_context_manager_sets_and_resets() -> None:
    ctx = _ctx()
    assert current_tenant_or_none() is None
    with tenant_context(ctx):
        assert current_tenant() is ctx
    assert current_tenant_or_none() is None


async def test_get_tenant_session_without_context_refuses() -> None:
    sessions = get_tenant_session()
    with pytest.raises(TenantContextError):
        await anext(sessions)


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("acme.app.example.fr", "acme"),
        ("acme.app.example.fr:443", "acme"),
        ("ACME.app.example.fr", "acme"),
        ("localhost", None),  # pas de sous-domaine
        ("app.example.fr", "app"),  # candidat syntaxiquement valide → 404 au catalogue
        ("_bad.app.example.fr", None),  # label invalide
        ("", None),
    ],
)
def test_extract_slug(host: str, expected: str | None) -> None:
    assert extract_slug(host) == expected


def _resolver_app() -> FastAPI:
    app = FastAPI()

    @app.get("/whoami")
    async def whoami(  # pyright: ignore[reportUnusedFunction]
        ctx: Annotated[TenantContext, Depends(resolve_tenant)],
    ) -> dict[str, str | None]:
        # La dépendance doit avoir posé le contexte pour toute la requête.
        assert current_tenant() is ctx
        return {"slug": ctx.slug, "role": ctx.role}

    return app


@requires_postgres
async def test_resolve_tenant_from_subdomain(db_env: Settings) -> None:
    """Phase 2 : le contexte tenant exige sous-domaine x session x membership."""
    async with get_control_sessionmaker()() as session:
        session.add_all(
            [
                Tenant(slug="acme", name="ACME", state=TenantState.ACTIVE),
                Tenant(slug="frozen", name="Frozen", state=TenantState.SUSPENDED),
                Tenant(
                    slug="gone",
                    name="Gone",
                    state=TenantState.ACTIVE,
                    deleted_at=datetime.now(UTC),
                ),
            ]
        )
        await session.commit()

    async with get_control_sessionmaker()() as session:
        acme = await session.scalar(select(Tenant).where(Tenant.slug == "acme"))
    assert acme is not None
    member = await create_user("member@example.com")
    member_token = await create_session_token(member.id)
    await add_membership(member.id, acme.id, "member")
    outsider = await create_user("outsider@example.com")
    outsider_token = await create_session_token(outsider.id)

    # TestClient exécute l'app dans sa propre event loop : on libère l'engine
    # singleton créé dans la boucle du test pour que l'app recrée le sien.
    from app.core.db import dispose_control_engine

    await dispose_control_engine()

    cookie = db_env.session_cookie_name
    with TestClient(_resolver_app()) as client:
        anonymous = client.get("/whoami", headers={"host": "acme.app.example.fr"})
        assert anonymous.status_code == 401

        client.cookies.set(cookie, outsider_token)
        not_member = client.get("/whoami", headers={"host": "acme.app.example.fr"})
        assert not_member.status_code == 403

        client.cookies.set(cookie, member_token)
        ok = client.get("/whoami", headers={"host": "acme.app.example.fr"})
        assert ok.status_code == 200
        assert ok.json() == {"slug": "acme", "role": "member"}

        unknown = client.get("/whoami", headers={"host": "nexiste-pas.app.example.fr"})
        assert unknown.status_code == 404

        suspended = client.get("/whoami", headers={"host": "frozen.app.example.fr"})
        assert suspended.status_code == 403

        # Soft-delete (ADR 0002) : indistinguable d'un tenant inexistant.
        deleted = client.get("/whoami", headers={"host": "gone.app.example.fr"})
        assert deleted.status_code == 404

        no_subdomain = client.get("/whoami", headers={"host": "localhost"})
        assert no_subdomain.status_code == 404

    # Le contexte ne fuit jamais hors requête.
    assert current_tenant_or_none() is None
