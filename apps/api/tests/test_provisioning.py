import pytest
from sqlalchemy import func, select

from app.core.config import Settings
from app.core.db import get_control_sessionmaker
from app.tenancy.context import TenantContext, tenant_context
from app.tenancy.migrations_runner import read_schema_revision
from app.tenancy.models import Tenant, TenantState
from app.tenancy.provisioning import (
    ProvisioningError,
    create_database,
    provision_tenant,
    retry_provision,
)
from app.tenancy.session import get_tenant_session
from app.tenancy.tenant_base import TenantSetting
from tests.conftest import requires_postgres

pytestmark = requires_postgres


async def _catalog_count() -> int:
    async with get_control_sessionmaker()() as session:
        return (await session.scalar(select(func.count()).select_from(Tenant))) or 0


async def _get_tenant(slug: str) -> Tenant | None:
    async with get_control_sessionmaker()() as session:
        return await session.scalar(select(Tenant).where(Tenant.slug == slug))


async def test_provision_tenant_end_to_end(db_env: Settings) -> None:
    tenant = await provision_tenant("acme", "ACME Corp")

    assert tenant.state is TenantState.ACTIVE
    assert tenant.db_name == f"{db_env.tenant_db_prefix}acme"

    # Base migrée à head.
    url = db_env.tenant_database_url(tenant.db_name, tenant.db_host)
    assert await read_schema_revision(url) == "0001_tenant"

    # Seed présent, lu via LE chemin officiel : contexte tenant + get_tenant_session.
    ctx = TenantContext(
        tenant_id=tenant.id,
        slug=tenant.slug,
        state=tenant.state,
        db_name=tenant.db_name,
        db_host=tenant.db_host,
    )
    with tenant_context(ctx):
        async for session in get_tenant_session():
            setting = await session.get(TenantSetting, "tenant:slug")
            assert setting is not None
            assert setting.value == "acme"


async def test_invalid_slug_rejected_without_touching_db(db_env: Settings) -> None:
    for bad_slug in ("A-majuscule", "-tiret", "x", "trop" + "o" * 40, "sous.domaine"):
        with pytest.raises(ValueError):
            await provision_tenant(bad_slug, "Bad")
    with pytest.raises(ValueError):
        await provision_tenant("admin", "Réservé")  # slug réservé plateforme
    assert await _catalog_count() == 0


async def test_duplicate_slug_rejected(db_env: Settings) -> None:
    await provision_tenant("acme", "ACME")
    with pytest.raises(ProvisioningError, match="déjà utilisé"):
        await provision_tenant("acme", "ACME encore")
    assert await _catalog_count() == 1


async def test_failure_then_retry_provision(db_env: Settings) -> None:
    # Panne injectée : la base existe déjà → CREATE DATABASE échoue à mi-parcours.
    db_name = f"{db_env.tenant_db_prefix}acme"
    await create_database(db_name, db_env)

    with pytest.raises(ProvisioningError):
        await provision_tenant("acme", "ACME")
    tenant = await _get_tenant("acme")
    assert tenant is not None
    assert tenant.state is TenantState.FAILED

    # La reprise droppe la base orpheline et rejoue tout.
    repaired = await retry_provision("acme")
    assert repaired.state is TenantState.ACTIVE
    url = db_env.tenant_database_url(repaired.db_name, repaired.db_host)
    assert await read_schema_revision(url) == "0001_tenant"


async def test_retry_provision_refuses_active_tenant(db_env: Settings) -> None:
    await provision_tenant("acme", "ACME")
    with pytest.raises(ProvisioningError, match="réservé aux provisionings en échec"):
        await retry_provision("acme")
