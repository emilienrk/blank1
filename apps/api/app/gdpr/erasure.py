"""Effacement RGPD d'un tenant (plan global §7, Phase 4 T5).

Machine à deux temps (décision D2) : `request_erasure` bascule immédiatement le
tenant en `pending_deletion` (inaccessible, `resolve_tenant` refuse comme
`suspended`) ; après le délai de grâce, la tâche beat `execute_pending_erasures`
exécute la destruction physique — irréversible, réutilise la mécanique de
`DROP DATABASE` validée par le provisioning (invariant I6 Phase 1). Aucun autre
chemin de drop n'existe (invariant de phase n°4).
"""

from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import Invitation
from app.core.config import Settings, get_settings
from app.core.db import get_control_sessionmaker
from app.directory.models import Membership, User
from app.gdpr.models import ErasureLog
from app.tenancy.engine_manager import get_engine_manager
from app.tenancy.models import Tenant, TenantState
from app.tenancy.provisioning import drop_database_if_exists

logger = structlog.get_logger()


class GdprErasureError(RuntimeError):
    """Demande/annulation d'effacement refusée (tenant inconnu, état incohérent)."""


def _now() -> datetime:
    return datetime.now(UTC)


async def request_erasure(slug: str) -> Tenant:
    """Bascule un tenant `active`/`suspended` en `pending_deletion` — 403 immédiat
    sur `resolve_tenant`, la destruction physique suit après le délai de grâce."""
    async with get_control_sessionmaker()() as session:
        tenant = await session.scalar(select(Tenant).where(Tenant.slug == slug))
        if tenant is None:
            msg = f"Tenant {slug!r} inconnu au catalogue."
            raise GdprErasureError(msg)
        if tenant.state is TenantState.PENDING_DELETION:
            msg = f"Effacement déjà demandé pour {slug!r}."
            raise GdprErasureError(msg)
        if tenant.state not in (TenantState.ACTIVE, TenantState.SUSPENDED):
            msg = (
                f"Le tenant {slug!r} est {tenant.state} — effacement réservé "
                "aux tenants actifs ou suspendus."
            )
            raise GdprErasureError(msg)
        tenant.state = TenantState.PENDING_DELETION
        tenant.deletion_requested_at = _now()
        await session.commit()
        await session.refresh(tenant)
    logger.info("gdpr_erasure_requested", tenant=slug)
    return tenant


async def cancel_erasure(slug: str) -> Tenant:
    """Ramène un tenant `pending_deletion` à `active` — uniquement pendant le délai
    de grâce (une fois la base droppée, il n'existe plus au catalogue)."""
    async with get_control_sessionmaker()() as session:
        tenant = await session.scalar(select(Tenant).where(Tenant.slug == slug))
        if tenant is None:
            msg = f"Tenant {slug!r} inconnu au catalogue."
            raise GdprErasureError(msg)
        if tenant.state is not TenantState.PENDING_DELETION:
            msg = f"Aucun effacement en cours pour {slug!r}."
            raise GdprErasureError(msg)
        tenant.state = TenantState.ACTIVE
        tenant.deletion_requested_at = None
        await session.commit()
        await session.refresh(tenant)
    logger.info("gdpr_erasure_cancelled", tenant=slug)
    return tenant


async def _purge_control_plane(
    session: AsyncSession, tenant: Tenant, requested_at: datetime
) -> None:
    """Purge memberships/invitations du tenant, les users devenus orphelins
    (décision D6 : plus aucun membership nulle part), puis la ligne catalogue —
    dans cet ordre, avant l'écriture de la trace minimale."""
    # Candidats à l'orphelinat : uniquement les membres DE CE TENANT — un
    # platform_admin sans membership nulle part, ou un orphelin d'un autre
    # effacement passé, ne doit jamais être touché par CETTE purge.
    affected_user_ids = list(
        (
            await session.scalars(
                select(Membership.user_id).where(Membership.tenant_id == tenant.id)
            )
        ).all()
    )

    await session.execute(delete(Membership).where(Membership.tenant_id == tenant.id))
    await session.execute(delete(Invitation).where(Invitation.tenant_id == tenant.id))
    await session.flush()

    if affected_user_ids:
        remaining_member_ids = select(Membership.user_id).distinct()
        orphan_ids = list(
            (
                await session.scalars(
                    select(User.id).where(
                        User.id.in_(affected_user_ids), ~User.id.in_(remaining_member_ids)
                    )
                )
            ).all()
        )
        if orphan_ids:
            await session.execute(delete(User).where(User.id.in_(orphan_ids)))

    session.add(ErasureLog(slug=tenant.slug, requested_at=requested_at))
    catalog_row = await session.get(Tenant, tenant.id)
    if catalog_row is not None:
        await session.delete(catalog_row)


async def _erase_one(tenant: Tenant, settings: Settings) -> None:
    await drop_database_if_exists(tenant.db_name, settings)
    await get_engine_manager().invalidate(tenant.id)

    async with get_control_sessionmaker()() as session:
        await _purge_control_plane(session, tenant, tenant.deletion_requested_at or _now())
        await session.commit()

    logger.info("gdpr_erasure_executed", tenant=tenant.slug)


async def execute_pending_erasures(*, settings: Settings | None = None) -> int:
    """Tâche beat (T6) : droppe les tenants dont le délai de grâce est écoulé.

    Même philosophie que le runner de migrations (invariant I5) : l'échec d'un
    tenant n'empêche pas les suivants."""
    settings = settings or get_settings()
    cutoff = _now() - timedelta(days=settings.gdpr_erasure_grace_days)
    async with get_control_sessionmaker()() as session:
        due = await session.scalars(
            select(Tenant).where(
                Tenant.state == TenantState.PENDING_DELETION,
                Tenant.deletion_requested_at <= cutoff,
            )
        )
        tenants = list(due.all())

    executed = 0
    for tenant in tenants:
        try:
            await _erase_one(tenant, settings)
            executed += 1
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"[:500]
            logger.error("gdpr_erasure_failed", tenant=tenant.slug, error=error)
    return executed
