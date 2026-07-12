"""Quotas mensuels soft par tenant (Phase 6 T5).

Compteur sur Valkey (`ai:quota:<tenant>:<AAAA-MM>`, incrément post-appel, TTL 62 j) :
rapide, sans requête SQL par appel. Recalé chaque jour par l'agrégat quotidien
(`reconcile`) — dérive bornée à la journée (risque F5 assumé).

Au-delà du quota, **l'appel passe** (soft limit, §6) mais un événement d'alerte est
loggé et audité (`core.ai.quota_exceeded`) UNE FOIS PAR JOUR par tenant, et le
back-office le dérive des agrégats. Le hard limit (`hard_limit_enabled`) est prévu
mais reste inopérant tant qu'il n'est pas exposé (défaut soft).
"""

# redis-py expose des types incomplets sur les commandes async.
# pyright: reportUnknownMemberType=false

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import redis.asyncio as aioredis
import structlog

from app.audit.service import record_audit_event
from app.core.config import get_settings
from app.tenancy.context import TenantContext
from app.tenancy.engine_manager import get_engine_manager

logger = structlog.get_logger()

MONTH_TTL_SECONDS = 62 * 24 * 3600
ALERT_TTL_SECONDS = 24 * 3600

_client: aioredis.Redis | None = None


def get_valkey_client() -> aioredis.Redis:
    global _client
    if _client is None:
        _client = aioredis.Redis.from_url(get_settings().valkey_url)
    return _client


def set_valkey_client(client: aioredis.Redis | None) -> None:
    """Injection pour les tests (fakeredis) ou réinitialisation."""
    global _client
    _client = client


def _month_key(tenant_id: uuid.UUID, now: datetime) -> str:
    return f"ai:quota:{tenant_id}:{now:%Y-%m}"


def _alert_key(tenant_id: uuid.UUID, now: datetime) -> str:
    return f"ai:quota:alert:{tenant_id}:{now:%Y-%m-%d}"


@dataclass(frozen=True, slots=True)
class QuotaOutcome:
    month_usage: int
    over_quota: bool
    alerted: bool


async def current_usage(tenant_id: uuid.UUID) -> int:
    raw = await get_valkey_client().get(_month_key(tenant_id, datetime.now(UTC)))
    return int(raw) if raw is not None else 0


async def add_usage(tenant_id: uuid.UUID, tokens: int) -> int:
    """Incrémente le compteur du mois courant ; retourne le nouveau total."""
    client = get_valkey_client()
    key = _month_key(tenant_id, datetime.now(UTC))
    new_total = int(await client.incrby(key, max(tokens, 0)))
    if new_total == tokens:  # première écriture du mois → pose le TTL
        await client.expire(key, MONTH_TTL_SECONDS)
    return new_total


async def reconcile(tenant_id: uuid.UUID, month_total: int) -> None:
    """Recale le compteur sur l'agrégat SQL (beat quotidien) : source de vérité
    différée, borne la dérive du compteur approximatif (risque F5)."""
    client = get_valkey_client()
    key = _month_key(tenant_id, datetime.now(UTC))
    await client.set(key, max(month_total, 0), ex=MONTH_TTL_SECONDS)


async def _emit_alert_once(ctx: TenantContext, month_usage: int, quota: int) -> bool:
    """Alerte auditée + loggée au plus une fois par jour et par tenant (SET NX)."""
    now = datetime.now(UTC)
    acquired = await get_valkey_client().set(
        _alert_key(ctx.tenant_id, now), "1", nx=True, ex=ALERT_TTL_SECONDS
    )
    if not acquired:
        return False
    logger.warning(
        "ai_quota_exceeded",
        tenant=ctx.slug,
        month_usage=month_usage,
        monthly_token_quota=quota,
    )
    # Audit en DB tenant (donnée du client) — session dédiée, committée seule :
    # l'action déclenchante (usage IA) vit en control-plane (bases distinctes).
    async with get_engine_manager().session(ctx) as session:
        await record_audit_event(
            session,
            action="core.ai.quota_exceeded",
            resource_type="ai_quota",
            resource_id=f"{now:%Y-%m}",
            payload={"month_usage": month_usage, "monthly_token_quota": quota},
        )
        await session.commit()
    return True


async def record_and_alert(ctx: TenantContext, *, added_tokens: int, quota: int) -> QuotaOutcome:
    """Post-appel : incrémente le compteur et, si le quota est franchi, alerte
    (audit + log) une fois par jour. L'appel n'est jamais bloqué (soft limit)."""
    month_usage = await add_usage(ctx.tenant_id, added_tokens)
    over_quota = month_usage > quota
    alerted = False
    if over_quota:
        alerted = await _emit_alert_once(ctx, month_usage, quota)
    return QuotaOutcome(month_usage=month_usage, over_quota=over_quota, alerted=alerted)
