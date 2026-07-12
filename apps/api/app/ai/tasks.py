"""Tâches Celery du gateway IA (Phase 6 T4).

Beat quotidien : agrégation de la veille dans `ai_usage_daily` (upsert idempotent,
rejouable) ; recalage des compteurs de quota Valkey sur la somme SQL du mois
(dérive bornée à la journée, T5) ; purge des événements bruts au-delà de
`ai_usage_raw_retention_days` — les AGRÉGATS, eux, sont conservés (fondation
facturation §2).
"""

# Celery n'expose pas de types (voir app/worker.py).
# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUntypedFunctionDecorator=false
# pyright: reportUnknownVariableType=false, reportCallIssue=false, reportUnknownArgumentType=false

import asyncio
from datetime import UTC, date, datetime, timedelta

import structlog
from celery import shared_task
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.ai import quota
from app.ai.models import AIUsageDaily, AIUsageEvent, UsageStatus
from app.core.config import get_settings
from app.core.db import dispose_control_engine, get_control_sessionmaker

logger = structlog.get_logger()


async def aggregate_day(target_day: date) -> int:
    """Agrège les événements de `target_day` dans `ai_usage_daily` (upsert idempotent).

    Retourne le nombre de lignes d'agrégat écrites/mises à jour. Rejouable : les
    valeurs SET sont les sommes recalculées, pas des incréments.
    """
    start = datetime.combine(target_day, datetime.min.time(), tzinfo=UTC)
    end = start + timedelta(days=1)
    async with get_control_sessionmaker()() as session:
        grouped = (
            await session.execute(
                select(
                    AIUsageEvent.tenant_id,
                    AIUsageEvent.provider,
                    AIUsageEvent.model,
                    func.sum(AIUsageEvent.input_tokens),
                    func.sum(AIUsageEvent.output_tokens),
                    func.sum(AIUsageEvent.cached_tokens),
                    func.count(),
                    func.count().filter(AIUsageEvent.status != UsageStatus.OK),
                    func.sum(AIUsageEvent.estimated_cost_microeur),
                )
                .where(AIUsageEvent.occurred_at >= start, AIUsageEvent.occurred_at < end)
                .group_by(AIUsageEvent.tenant_id, AIUsageEvent.provider, AIUsageEvent.model)
            )
        ).all()

        written = 0
        for row in grouped:
            values = {
                "day": target_day,
                "tenant_id": row[0],
                "provider": row[1],
                "model": row[2],
                "input_tokens": int(row[3] or 0),
                "output_tokens": int(row[4] or 0),
                "cached_tokens": int(row[5] or 0),
                "request_count": int(row[6] or 0),
                "error_count": int(row[7] or 0),
                "estimated_cost_microeur": int(row[8] or 0),
            }
            stmt = pg_insert(AIUsageDaily).values(**values)
            keys = ("day", "tenant_id", "provider", "model")
            stmt = stmt.on_conflict_do_update(
                index_elements=list(keys),
                set_={k: values[k] for k in values if k not in keys},
            )
            await session.execute(stmt)
            written += 1
        await session.commit()
    return written


async def reconcile_month_quotas() -> None:
    """Recale les compteurs Valkey sur la somme SQL du mois courant (T5)."""
    now = datetime.now(UTC)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    async with get_control_sessionmaker()() as session:
        rows = (
            await session.execute(
                select(
                    AIUsageEvent.tenant_id,
                    func.sum(AIUsageEvent.input_tokens + AIUsageEvent.output_tokens),
                )
                .where(AIUsageEvent.occurred_at >= month_start)
                .group_by(AIUsageEvent.tenant_id)
            )
        ).all()
    for tenant_id, total in rows:
        await quota.reconcile(tenant_id, int(total or 0))


async def purge_raw_events() -> int:
    """Supprime les événements bruts au-delà de la rétention (agrégats conservés)."""
    cutoff = datetime.now(UTC) - timedelta(days=get_settings().ai_usage_raw_retention_days)
    async with get_control_sessionmaker()() as session:
        result = await session.execute(
            delete(AIUsageEvent).where(AIUsageEvent.occurred_at < cutoff)
        )
        await session.commit()
        return int(getattr(result, "rowcount", 0) or 0)


async def _run_daily_rollup() -> dict[str, int]:
    try:
        yesterday = (datetime.now(UTC) - timedelta(days=1)).date()
        aggregated = await aggregate_day(yesterday)
        await reconcile_month_quotas()
        purged = await purge_raw_events()
        logger.info(
            "ai_usage_rollup",
            day=yesterday.isoformat(),
            aggregated=aggregated,
            purged=purged,
        )
        return {"aggregated": aggregated, "purged": purged}
    finally:
        # Pools asyncpg liés à leur event loop (cf. app/gdpr/tasks.py).
        await dispose_control_engine()


@shared_task(name="core.ai.daily_usage_rollup")
def daily_usage_rollup_task() -> dict[str, int]:
    return asyncio.run(_run_daily_rollup())
