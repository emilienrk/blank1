# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
"""Quotas soft + alerte (Phase 6 T5).

Compteur Valkey (fakeredis) : incrément, lecture, recalage. Dépassement → l'appel
passe (soft), l'alerte est auditée UNE FOIS PAR JOUR (SET NX). L'audit s'écrit en
DB tenant (Postgres réel).
"""

from collections.abc import Iterator

import pytest
from sqlalchemy import select

from app.ai import quota
from app.audit.tenant_models import AuditEvent
from app.core.config import Settings
from app.tenancy.context import tenant_context
from app.tenancy.engine_manager import get_engine_manager
from app.tenancy.provisioning import provision_tenant
from tests.ai_helpers import (
    ctx_for,
    ctx_stub,
    install_fake_quota_valkey,
    reset_quota_valkey,
)
from tests.conftest import requires_postgres
from tests.helpers import reset_db_engines


@pytest.fixture(autouse=True)
def fake_quota_valkey() -> Iterator[None]:
    install_fake_quota_valkey()
    yield
    reset_quota_valkey()


async def test_counter_incremented_and_read() -> None:
    ctx = ctx_stub()
    assert await quota.current_usage(ctx.tenant_id) == 0
    assert await quota.add_usage(ctx.tenant_id, 100) == 100
    assert await quota.add_usage(ctx.tenant_id, 50) == 150
    assert await quota.current_usage(ctx.tenant_id) == 150


async def test_reconcile_resets_counter_to_aggregate() -> None:
    # Recalage par l'agrégat quotidien (dérive bornée à la journée, risque F5).
    ctx = ctx_stub()
    await quota.add_usage(ctx.tenant_id, 999)
    await quota.reconcile(ctx.tenant_id, 250)
    assert await quota.current_usage(ctx.tenant_id) == 250


async def test_under_quota_does_not_alert() -> None:
    ctx = ctx_stub()
    outcome = await quota.record_and_alert(ctx, added_tokens=10, quota=1_000)
    assert outcome.over_quota is False
    assert outcome.alerted is False


@requires_postgres
async def test_over_quota_alerts_once_per_day(db_env: Settings) -> None:
    tenant = await provision_tenant("acme", "ACME")
    await reset_db_engines()
    install_fake_quota_valkey()  # nouvelle boucle après reset
    ctx = ctx_for(tenant)

    first = await quota.record_and_alert(ctx, added_tokens=5_000, quota=1_000)
    second = await quota.record_and_alert(ctx, added_tokens=5_000, quota=1_000)

    assert first.over_quota and first.alerted is True
    # Deuxième dépassement le même jour : l'appel passe, mais pas de nouvelle alerte.
    assert second.over_quota and second.alerted is False

    with tenant_context(ctx):
        async with get_engine_manager().session(ctx) as session:
            events = list((await session.scalars(select(AuditEvent))).all())
    quota_events = [e for e in events if e.action == "core.ai.quota_exceeded"]
    assert len(quota_events) == 1
