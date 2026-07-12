"""Cadre de rétention/purge configurable (plan global §7, Phase 4 T6).

Registre générique que les phases suivantes rempliront (connecteurs, modules
métier) : chaque type de donnée purgeable s'enregistre avec une clé, une durée
par défaut et une fonction de purge prenant la session tenant courante et une
date limite. Première politique : `audit_events` (déf. `audit_retention_days`).
Surcharge par tenant via `tenant_settings` (clé `retention.<type>`, en jours).
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import structlog
from sqlalchemy import CursorResult, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.tenant_models import AuditEvent
from app.core.config import get_settings
from app.tenancy.tenant_base import TenantSetting

logger = structlog.get_logger()

# Bornage des lots de purge (décision D7) : un DELETE massif tient des verrous
# longs et gonfle le WAL ; le lot commité est le pattern standard.
BATCH_SIZE = 5_000


@dataclass(slots=True)
class RetentionPolicy:
    key: str
    default_days: int
    purge: Callable[[AsyncSession, datetime], Awaitable[int]]


_REGISTRY: dict[str, RetentionPolicy] = {}


def register_policy(policy: RetentionPolicy) -> None:
    _REGISTRY[policy.key] = policy


def registered_policies() -> list[RetentionPolicy]:
    return list(_REGISTRY.values())


async def _purge_audit_events(session: AsyncSession, cutoff: datetime) -> int:
    """Purge par lots (décision D7) : commit intermédiaire à chaque lot, un
    tenant/lot en échec n'empêche pas les suivants (même philosophie que le
    runner de migrations, invariant I5)."""
    total = 0
    while True:
        result = await session.execute(
            delete(AuditEvent).where(
                AuditEvent.id.in_(
                    select(AuditEvent.id).where(AuditEvent.occurred_at < cutoff).limit(BATCH_SIZE)
                )
            )
        )
        deleted = max(cast(CursorResult[Any], result).rowcount, 0)
        total += deleted
        await session.commit()
        if deleted < BATCH_SIZE:
            break
    return total


def _register_default_policies() -> None:
    """(Ré)enregistre les politiques du socle avec la config courante — `get_settings()`
    est mis en cache mais peut changer entre deux exécutions (tests notamment)."""
    register_policy(
        RetentionPolicy(
            key="audit_events",
            default_days=get_settings().audit_retention_days,
            purge=_purge_audit_events,
        )
    )


async def _effective_retention_days(session: AsyncSession, key: str, default_days: int) -> int:
    override = await session.get(TenantSetting, f"retention.{key}")
    if override is None:
        return default_days
    try:
        return int(override.value)
    except ValueError:
        logger.warning("retention_override_invalid", key=key, value=override.value)
        return default_days


async def apply_tenant_policies(session: AsyncSession) -> dict[str, int]:
    """Applique toutes les politiques enregistrées sur la session tenant COURANTE ;
    retourne le nombre de lignes purgées par type (rapport JSON, sans PII)."""
    _register_default_policies()
    report: dict[str, int] = {}
    for policy in registered_policies():
        days = await _effective_retention_days(session, policy.key, policy.default_days)
        cutoff = datetime.now(UTC) - timedelta(days=days)
        report[policy.key] = await policy.purge(session, cutoff)
    return report
