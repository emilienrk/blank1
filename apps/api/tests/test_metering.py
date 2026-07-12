# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
"""Metering (Phase 6 T4) : best-effort (D3), agrégat idempotent, purge des bruts.

L'échec d'insertion ne casse jamais la réponse IA (décision D3). L'agrégation
quotidienne est rejouable ; la purge des événements bruts conserve les agrégats
(fondation facturation §2).
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.ai import metering
from app.ai.metering import UsageRecord, record_usage
from app.ai.models import AIUsageDaily, AIUsageEvent, UsageStatus
from app.ai.tasks import aggregate_day, purge_raw_events
from app.core.config import Settings
from app.core.db import get_control_sessionmaker
from tests.conftest import requires_postgres
from tests.helpers import add_catalog_tenant


def _record(tenant_id: uuid.UUID, **overrides: object) -> UsageRecord:
    base: dict[str, object] = {
        "tenant_id": tenant_id,
        "module": "core",
        "provider": "mistral",
        "model": "mistral-small-latest",
        "status": UsageStatus.OK,
        "price_version": "2026-07-12",
        "input_tokens": 10,
        "output_tokens": 5,
        "estimated_cost_microeur": 42,
    }
    base.update(overrides)
    return UsageRecord(**base)  # type: ignore[arg-type]


async def test_insert_failure_does_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    # DB indisponible simulée : record_usage logge mais ne lève pas (D3).
    def _boom() -> object:
        raise RuntimeError("control-plane indisponible")

    logged: list[str] = []

    def _capture(event: str, **_: object) -> None:
        logged.append(event)

    monkeypatch.setattr(metering, "get_control_sessionmaker", _boom)
    monkeypatch.setattr(metering.logger, "exception", _capture)

    await record_usage(_record(uuid.uuid4()))  # ne doit pas lever
    assert logged == ["ai_metering_insert_failed"]


@requires_postgres
async def test_daily_aggregate_is_idempotent(db_env: Settings) -> None:
    tenant = await add_catalog_tenant("acme")
    day = datetime.now(UTC).date()
    occurred = datetime.combine(day, datetime.min.time(), tzinfo=UTC) + timedelta(hours=6)
    async with get_control_sessionmaker()() as session:
        for _ in range(2):
            session.add(
                AIUsageEvent(
                    tenant_id=tenant.id,
                    module="core",
                    provider="mistral",
                    model="mistral-small-latest",
                    input_tokens=10,
                    output_tokens=5,
                    estimated_cost_microeur=42,
                    price_version="2026-07-12",
                    status=UsageStatus.OK,
                    occurred_at=occurred,
                )
            )
        await session.commit()

    # Deux passages → mêmes valeurs (SET recalculé, pas incrément).
    await aggregate_day(day)
    await aggregate_day(day)

    async with get_control_sessionmaker()() as session:
        rows = list((await session.scalars(select(AIUsageDaily))).all())
    assert len(rows) == 1
    assert rows[0].input_tokens == 20
    assert rows[0].output_tokens == 10
    assert rows[0].request_count == 2


@requires_postgres
async def test_purge_removes_old_raw_events_but_keeps_aggregates(db_env: Settings) -> None:
    tenant = await add_catalog_tenant("acme")
    now = datetime.now(UTC)
    old_day = (now - timedelta(days=200)).date()
    old_occurred = datetime.combine(old_day, datetime.min.time(), tzinfo=UTC) + timedelta(hours=1)
    async with get_control_sessionmaker()() as session:
        session.add(
            AIUsageEvent(
                tenant_id=tenant.id,
                provider="mistral",
                model="mistral-small-latest",
                input_tokens=100,
                output_tokens=50,
                price_version="2026-07-12",
                status=UsageStatus.OK,
                occurred_at=old_occurred,
            )
        )
        session.add(
            AIUsageEvent(
                tenant_id=tenant.id,
                provider="mistral",
                model="mistral-small-latest",
                input_tokens=1,
                output_tokens=1,
                price_version="2026-07-12",
                status=UsageStatus.OK,
                occurred_at=now,
            )
        )
        await session.commit()

    await aggregate_day(old_day)  # agrégat conservé au-delà de la rétention
    purged = await purge_raw_events()
    assert purged == 1

    async with get_control_sessionmaker()() as session:
        events = list((await session.scalars(select(AIUsageEvent))).all())
        aggregates = list((await session.scalars(select(AIUsageDaily))).all())
    assert len(events) == 1  # le récent survit
    assert len(aggregates) == 1  # l'agrégat de la vieille journée demeure
