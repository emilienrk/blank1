# TestClient (starlette/httpx) expose des membres partiellement typés.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
"""Effacement RGPD (Phase 4 T5) : demande → pending_deletion → 403 immédiat,
annulation, exécution après le délai de grâce (drop, purge catalogue, orphelins,
erasure_log), garde-fous sur slug inconnu/re-demande."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import Settings
from app.core.db import get_control_sessionmaker
from app.directory.models import Membership, User
from app.gdpr.erasure import (
    GdprErasureError,
    cancel_erasure,
    execute_pending_erasures,
    request_erasure,
)
from app.gdpr.models import ErasureLog
from app.main import create_app
from app.tenancy.migrations_runner import read_schema_revision
from app.tenancy.models import Tenant, TenantState
from app.tenancy.provisioning import provision_tenant
from tests.conftest import requires_postgres
from tests.helpers import add_membership, create_session_token, create_user, reset_db_engines

pytestmark = requires_postgres


async def test_request_erasure_blocks_access_immediately(db_env: Settings) -> None:
    tenant = await provision_tenant("acme", "ACME")
    member = await create_user("bob@example.com")
    await add_membership(member.id, tenant.id, "member")
    member_token = await create_session_token(member.id)
    await reset_db_engines()

    updated = await request_erasure("acme")
    assert updated.state is TenantState.PENDING_DELETION
    assert updated.deletion_requested_at is not None
    await reset_db_engines()

    host = {"host": "acme.app.example.fr"}
    with TestClient(create_app()) as client:
        client.cookies.set(db_env.session_cookie_name, member_token)
        response = client.get("/api/v1/directory/members", headers=host)
        assert response.status_code == 403


async def test_request_erasure_twice_rejected(db_env: Settings) -> None:
    await provision_tenant("acme", "ACME")
    await request_erasure("acme")
    with pytest.raises(GdprErasureError, match="déjà demandé"):
        await request_erasure("acme")


async def test_request_erasure_unknown_slug_rejected(db_env: Settings) -> None:
    with pytest.raises(GdprErasureError, match="inconnu"):
        await request_erasure("nexiste-pas")


async def test_cancel_erasure_restores_access(db_env: Settings) -> None:
    tenant = await provision_tenant("acme", "ACME")
    await request_erasure("acme")

    restored = await cancel_erasure("acme")
    assert restored.state is TenantState.ACTIVE
    assert restored.deletion_requested_at is None

    async with get_control_sessionmaker()() as session:
        row = await session.get(Tenant, tenant.id)
        assert row is not None
        assert row.state is TenantState.ACTIVE


async def test_cancel_erasure_without_pending_request_rejected(db_env: Settings) -> None:
    await provision_tenant("acme", "ACME")
    with pytest.raises(GdprErasureError, match="Aucun effacement"):
        await cancel_erasure("acme")


async def test_execute_pending_erasures_drops_database_and_purges_catalog(
    db_env: Settings,
) -> None:
    tenant = await provision_tenant("globex", "Globex")
    solo_member = await create_user("solo@example.com")  # membre du seul tenant effacé
    await add_membership(solo_member.id, tenant.id, "member")

    other_tenant = await provision_tenant("acme", "ACME")
    multi_member = await create_user("multi@example.com")  # membre des deux tenants
    await add_membership(multi_member.id, tenant.id, "admin")
    await add_membership(multi_member.id, other_tenant.id, "member")

    # Sans aucun membership nulle part (ex. platform_admin pur back-office) : jamais
    # candidat à l'orphelinat d'un tenant dont il n'a jamais été membre.
    bystander = await create_user("bystander@example.com")

    await request_erasure("globex")
    # Grâce expirée : on recule la date de demande plutôt que d'attendre en test.
    async with get_control_sessionmaker()() as session:
        row = await session.get(Tenant, tenant.id)
        assert row is not None
        row.deletion_requested_at = datetime.now(UTC) - timedelta(
            days=db_env.gdpr_erasure_grace_days + 1
        )
        await session.commit()

    executed = await execute_pending_erasures(settings=db_env)
    assert executed == 1

    async with get_control_sessionmaker()() as session:
        assert await session.get(Tenant, tenant.id) is None
        # Le membre exclusif de `globex` a disparu (décision D6) ; le membre multi-tenant reste.
        assert await session.get(User, solo_member.id) is None
        assert await session.get(User, multi_member.id) is not None
        # Bug de régression : un user sans membership nulle part mais étranger au
        # tenant effacé ne doit jamais être supprimé par CETTE purge.
        assert await session.get(User, bystander.id) is not None
        remaining_memberships = (
            await session.scalars(select(Membership).where(Membership.user_id == multi_member.id))
        ).all()
        assert {m.tenant_id for m in remaining_memberships} == {other_tenant.id}

        log = await session.scalar(select(ErasureLog).where(ErasureLog.slug == "globex"))
        assert log is not None
        assert log.requested_at is not None

    # La base physique n'existe plus.
    url = db_env.tenant_database_url(tenant.db_name, tenant.db_host)
    assert await read_schema_revision(url) is None


async def test_execute_pending_erasures_skips_tenants_still_in_grace_period(
    db_env: Settings,
) -> None:
    await provision_tenant("acme", "ACME")
    await request_erasure("acme")

    executed = await execute_pending_erasures(settings=db_env)
    assert executed == 0

    async with get_control_sessionmaker()() as session:
        row = await session.scalar(select(Tenant).where(Tenant.slug == "acme"))
        assert row is not None
        assert row.state is TenantState.PENDING_DELETION
