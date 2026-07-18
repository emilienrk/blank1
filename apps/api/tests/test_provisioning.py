import pytest
from sqlalchemy import func, select, text

from app.core.config import Settings
from app.core.db import get_control_sessionmaker
from app.tenancy.models import Tenant, TenantState
from app.tenancy.provisioning import ProvisioningError, provision_tenant
from tests.conftest import requires_postgres

pytestmark = requires_postgres


async def _catalog_count() -> int:
    async with get_control_sessionmaker()() as session:
        return (await session.scalar(select(func.count()).select_from(Tenant))) or 0


async def test_provision_tenant_inserts_active_and_audits(db_env: Settings) -> None:
    tenant = await provision_tenant("acme", "ACME Corp")

    assert tenant.state is TenantState.ACTIVE
    assert tenant.deleted_at is None

    # L'audit `core.tenant.provisioned` est écrit dans la MÊME transaction (ADR 0001),
    # estampillé du bon tenant_id par les garde-fous de session (lecture SQL brute :
    # on vérifie la colonne, pas le filtre automatique).
    async with get_control_sessionmaker()() as session:
        row = (
            await session.execute(text("SELECT tenant_id, action, actor_label FROM audit_events"))
        ).one()
        assert str(row[0]) == str(tenant.id)
        assert row[1] == "core.tenant.provisioned"
        assert row[2] == "cli"


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
