"""Back-office IA (Phase 6 T6) : agrégats d'usage + gestion des politiques par tenant.

Hors contexte tenant (control-plane), derrière `require_platform_admin`. La
politique se gère AU BACK-OFFICE (onboarding manuel assumé) — pas d'UI tenant dans
cette phase. Chaque changement de politique est audité (`core.ai.policy_changed`).
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.models import AIProvider, AIUsageDaily, TenantAIPolicy
from app.audit.service import record_audit_event_for_tenant
from app.core.config import get_settings
from app.tenancy.models import Tenant

KNOWN_PROVIDERS: frozenset[str] = frozenset(p.value for p in AIProvider)


class AIPolicyError(ValueError):
    """Requête de politique invalide (tenant inconnu, provider hors liste)."""


def _month_bounds(month: str | None) -> tuple[date, date]:
    """Renvoie (premier jour du mois, premier jour du mois suivant)."""
    now = datetime.now(UTC)
    if month:
        year, mon = (int(part) for part in month.split("-", 1))
    else:
        year, mon = now.year, now.month
    start = date(year, mon, 1)
    nxt = date(year + (mon // 12), (mon % 12) + 1, 1)
    return start, nxt


@dataclass(frozen=True, slots=True)
class TenantUsage:
    tenant_id: uuid.UUID
    slug: str
    name: str
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    request_count: int
    error_count: int
    estimated_cost_microeur: int
    total_tokens: int
    monthly_token_quota: int
    over_quota: bool


async def list_usage(session: AsyncSession, month: str | None = None) -> list[TenantUsage]:
    """Agrégats d'usage par tenant pour un mois (défaut : mois courant), issus des
    agrégats journaliers (`ai_usage_daily`) alimentés par le beat (T4)."""
    start, end = _month_bounds(month)
    grouped = (
        await session.execute(
            select(
                AIUsageDaily.tenant_id,
                func.sum(AIUsageDaily.input_tokens),
                func.sum(AIUsageDaily.output_tokens),
                func.sum(AIUsageDaily.cached_tokens),
                func.sum(AIUsageDaily.request_count),
                func.sum(AIUsageDaily.error_count),
                func.sum(AIUsageDaily.estimated_cost_microeur),
            )
            .where(AIUsageDaily.day >= start, AIUsageDaily.day < end)
            .group_by(AIUsageDaily.tenant_id)
        )
    ).all()

    tenants = {t.id: t for t in (await session.scalars(select(Tenant))).all()}
    quota_overrides = (
        await session.execute(select(TenantAIPolicy.tenant_id, TenantAIPolicy.monthly_token_quota))
    ).all()
    default_quota = get_settings().ai_quota_default_monthly_tokens
    quotas = {tid: q for tid, q in quota_overrides if q is not None}

    result: list[TenantUsage] = []
    for row in grouped:
        tenant = tenants.get(row[0])
        if tenant is None:
            continue
        input_tokens = int(row[1] or 0)
        output_tokens = int(row[2] or 0)
        total_tokens = input_tokens + output_tokens
        quota = quotas.get(row[0], default_quota)
        result.append(
            TenantUsage(
                tenant_id=row[0],
                slug=tenant.slug,
                name=tenant.name,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_tokens=int(row[3] or 0),
                request_count=int(row[4] or 0),
                error_count=int(row[5] or 0),
                estimated_cost_microeur=int(row[6] or 0),
                total_tokens=total_tokens,
                monthly_token_quota=quota,
                over_quota=total_tokens > quota,
            )
        )
    result.sort(key=lambda u: u.total_tokens, reverse=True)
    return result


@dataclass(frozen=True, slots=True)
class PolicyView:
    slug: str
    default_provider: str | None
    default_model: str | None
    allowed_providers: list[str]
    zero_retention: bool
    monthly_token_quota: int | None
    hard_limit_enabled: bool
    fallback_provider: str | None
    fallback_model: str | None
    byok_configured: bool


def _view(slug: str, record: TenantAIPolicy | None) -> PolicyView:
    if record is None:
        return PolicyView(
            slug=slug,
            default_provider=None,
            default_model=None,
            allowed_providers=[],
            zero_retention=False,
            monthly_token_quota=None,
            hard_limit_enabled=False,
            fallback_provider=None,
            fallback_model=None,
            byok_configured=False,
        )
    return PolicyView(
        slug=slug,
        default_provider=record.default_provider,
        default_model=record.default_model,
        allowed_providers=list(record.allowed_providers),
        zero_retention=record.zero_retention,
        monthly_token_quota=record.monthly_token_quota,
        hard_limit_enabled=record.hard_limit_enabled,
        fallback_provider=record.fallback_provider,
        fallback_model=record.fallback_model,
        # On expose seulement l'EXISTENCE d'une clé BYOK, jamais la clé (décision D7).
        byok_configured=record.byok_keys_enc is not None,
    )


async def _get_tenant(session: AsyncSession, slug: str) -> Tenant:
    tenant = (await session.scalars(select(Tenant).where(Tenant.slug == slug))).first()
    if tenant is None:
        raise AIPolicyError(f"Tenant introuvable : {slug!r}")
    return tenant


async def get_policy_view(session: AsyncSession, slug: str) -> PolicyView:
    tenant = await _get_tenant(session, slug)
    record = await session.get(TenantAIPolicy, tenant.id)
    return _view(slug, record)


@dataclass(frozen=True, slots=True)
class PolicyUpdate:
    default_provider: str | None
    default_model: str | None
    allowed_providers: list[str]
    zero_retention: bool
    monthly_token_quota: int | None
    hard_limit_enabled: bool
    fallback_provider: str | None
    fallback_model: str | None


def _validate_provider(name: str | None) -> None:
    if name is not None and name not in KNOWN_PROVIDERS:
        known = ", ".join(sorted(KNOWN_PROVIDERS))
        raise AIPolicyError(f"Provider inconnu : {name!r} (attendu : {known})")


async def set_policy(
    session: AsyncSession, slug: str, update: PolicyUpdate, *, actor_user_id: uuid.UUID
) -> PolicyView:
    """Upsert de la politique + audit `core.ai.policy_changed` (T6). Ne touche JAMAIS
    au champ BYOK (préparé, hors UI — décision D7)."""
    _validate_provider(update.default_provider)
    _validate_provider(update.fallback_provider)
    for provider in update.allowed_providers:
        _validate_provider(provider)

    tenant = await _get_tenant(session, slug)
    record = await session.get(TenantAIPolicy, tenant.id)
    if record is None:
        record = TenantAIPolicy(tenant_id=tenant.id)
        session.add(record)
    record.default_provider = update.default_provider
    record.default_model = update.default_model
    record.allowed_providers = list(update.allowed_providers)
    record.zero_retention = update.zero_retention
    record.monthly_token_quota = update.monthly_token_quota
    record.hard_limit_enabled = update.hard_limit_enabled
    record.fallback_provider = update.fallback_provider
    record.fallback_model = update.fallback_model
    await session.commit()

    # Audit en DB tenant (donnée du client), committé seul : politique en
    # control-plane, audit tenant — deux bases distinctes (cf. record_audit_event_for_tenant).
    await record_audit_event_for_tenant(
        tenant,
        action="core.ai.policy_changed",
        resource_type="ai_policy",
        resource_id=slug,
        payload={
            "default_provider": update.default_provider,
            "default_model": update.default_model,
            "allowed_providers": list(update.allowed_providers),
            "zero_retention": update.zero_retention,
            "monthly_token_quota": update.monthly_token_quota,
            "fallback_provider": update.fallback_provider,
        },
        actor_user_id=actor_user_id,
    )
    return _view(slug, record)
