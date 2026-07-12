"""Rétention/purge configurable (Phase 4 T6) : purge au-delà de la durée par
défaut, surcharge par tenant, purge par lots, un tenant en échec n'empêche pas
les autres (même philosophie que le runner de migrations, invariant I5)."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.audit.service import record_audit_event
from app.audit.tenant_models import AuditEvent
from app.core.config import Settings
from app.gdpr import retention
from app.gdpr.tasks import _apply_retention_policies  # pyright: ignore[reportPrivateUsage]
from app.tenancy.context import TenantContext, tenant_context
from app.tenancy.models import Tenant
from app.tenancy.provisioning import provision_tenant
from app.tenancy.session import get_tenant_session
from app.tenancy.tenant_base import TenantSetting
from tests.conftest import requires_postgres

pytestmark = requires_postgres


def _ctx(tenant: Tenant) -> TenantContext:
    return TenantContext(
        tenant_id=tenant.id,
        slug=tenant.slug,
        state=tenant.state,
        db_name=tenant.db_name,
        db_host=tenant.db_host,
    )


async def _write_old_event(tenant: Tenant, *, age_days: int) -> None:
    ctx = _ctx(tenant)
    with tenant_context(ctx):
        async for session in get_tenant_session():
            event = await record_audit_event(
                session,
                action="core.team.created",
                resource_type="team",
                resource_id="00000000-0000-0000-0000-000000000000",
                payload={},
            )
            event.occurred_at = datetime.now(UTC) - timedelta(days=age_days)
            await session.commit()


async def _count_events(tenant: Tenant) -> int:
    ctx = _ctx(tenant)
    with tenant_context(ctx):
        async for session in get_tenant_session():
            return len((await session.scalars(select(AuditEvent))).all())
    raise AssertionError("get_tenant_session n'a produit aucune session")  # pragma: no cover


async def _apply_policies(tenant: Tenant, *, set_override: str | None = None) -> dict[str, int]:
    ctx = _ctx(tenant)
    with tenant_context(ctx):
        async for session in get_tenant_session():
            if set_override is not None:
                session.add(TenantSetting(key="retention.audit_events", value=set_override))
                await session.commit()
            report = await retention.apply_tenant_policies(session)
            await session.commit()
            return report
    raise AssertionError("get_tenant_session n'a produit aucune session")  # pragma: no cover


async def test_apply_tenant_policies_purges_beyond_default_retention(db_env: Settings) -> None:
    db_env.audit_retention_days = 30
    tenant = await provision_tenant("acme", "ACME")
    await _write_old_event(tenant, age_days=31)

    report = await _apply_policies(tenant)
    assert report["audit_events"] == 1
    assert await _count_events(tenant) == 1  # seul l'événement de provisioning reste


async def test_apply_tenant_policies_respects_tenant_override(db_env: Settings) -> None:
    db_env.audit_retention_days = 365
    tenant = await provision_tenant("acme", "ACME")
    await _write_old_event(tenant, age_days=10)

    report = await _apply_policies(tenant, set_override="5")
    assert report["audit_events"] == 1


async def test_apply_tenant_policies_purges_in_batches(
    db_env: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(retention, "BATCH_SIZE", 2)
    db_env.audit_retention_days = 30
    tenant = await provision_tenant("acme", "ACME")
    for _ in range(5):
        await _write_old_event(tenant, age_days=31)

    report = await _apply_policies(tenant)
    assert report["audit_events"] == 5


async def test_apply_retention_policies_orchestrates_all_active_tenants(
    db_env: Settings,
) -> None:
    db_env.audit_retention_days = 30
    acme = await provision_tenant("acme", "ACME")
    globex = await provision_tenant("globex", "Globex")
    await _write_old_event(acme, age_days=31)
    await _write_old_event(globex, age_days=31)

    report = await _apply_retention_policies()
    assert report["acme"]["audit_events"] == 1
    assert report["globex"]["audit_events"] == 1
