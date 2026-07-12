"""Orchestration du back-office (Phase 3 T6) : hors contexte tenant, entièrement
derrière `require_platform_admin`.

Réutilise `provisioning.py` et `migrations_runner.py` TELS QUELS (aucune
duplication de la logique déjà exercée par le CLI, décision D6/T6).
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.models import MigrationOutcomeDict, MigrationReportRecord, MigrationRunStatus
from app.auth.permissions import ROLE_OWNER
from app.auth.service import get_user_by_email
from app.core.config import get_settings
from app.core.db import get_control_sessionmaker
from app.directory.models import Membership, User
from app.directory.service import accept_url_for, create_invitation
from app.tenancy.migrations_runner import (
    MigrationOutcome,
    MigrationsLockedError,
    read_schema_revision,
    upgrade_all,
)
from app.tenancy.models import Tenant

logger = structlog.get_logger()


def _now() -> datetime:
    return datetime.now(UTC)


# --- Tenants ---


@dataclass(slots=True)
class TenantSummary:
    id: uuid.UUID
    slug: str
    name: str
    state: str
    plan: str
    db_name: str
    schema_revision: str | None
    deletion_requested_at: datetime | None
    erasure_due_at: datetime | None


async def tenant_summary(tenant: Tenant) -> TenantSummary:
    settings = get_settings()
    url = settings.tenant_database_url(tenant.db_name, tenant.db_host)
    revision = await read_schema_revision(url)
    erasure_due_at = (
        tenant.deletion_requested_at + timedelta(days=settings.gdpr_erasure_grace_days)
        if tenant.deletion_requested_at is not None
        else None
    )
    return TenantSummary(
        id=tenant.id,
        slug=tenant.slug,
        name=tenant.name,
        state=str(tenant.state),
        plan=tenant.plan,
        db_name=tenant.db_name,
        schema_revision=revision,
        deletion_requested_at=tenant.deletion_requested_at,
        erasure_due_at=erasure_due_at,
    )


async def list_tenants(session: AsyncSession) -> list[TenantSummary]:
    tenants = await session.scalars(select(Tenant).order_by(Tenant.slug))
    return [await tenant_summary(tenant) for tenant in tenants]


async def create_tenant_invitation(
    session: AsyncSession, tenant_id: uuid.UUID, owner_email: str
) -> str:
    """Invite le premier owner (mêmes règles que `saas tenant create --owner-email`)."""
    _, token = await create_invitation(session, tenant_id, owner_email, ROLE_OWNER, actor_role=None)
    await session.commit()
    return accept_url_for(token)


# --- Utilisateurs (diagnostic support) ---


@dataclass(slots=True)
class UserLookup:
    user: User
    memberships: list[tuple[str, str]]


async def lookup_user(session: AsyncSession, email: str) -> UserLookup | None:
    user = await get_user_by_email(session, email)
    if user is None:
        return None
    rows = await session.execute(
        select(Tenant.slug, Membership.role)
        .join(Membership, Membership.tenant_id == Tenant.id)
        .where(Membership.user_id == user.id)
        .order_by(Tenant.slug)
    )
    return UserLookup(user=user, memberships=[(slug, role) for slug, role in rows.all()])


# --- Migrations (décision D6 : Celery + rapport persisté + polling) ---


def _outcome_to_dict(outcome: MigrationOutcome) -> MigrationOutcomeDict:
    return MigrationOutcomeDict(
        database=outcome.database,
        target=outcome.target,
        ok=outcome.ok,
        revision=outcome.revision,
        error=outcome.error,
    )


async def start_migration_report(session: AsyncSession) -> MigrationReportRecord:
    """Enregistre un rapport `running` — retour immédiat à l'appelant HTTP."""
    record = MigrationReportRecord(status=MigrationRunStatus.RUNNING)
    session.add(record)
    await session.flush()
    return record


async def execute_migration_report(report_id: uuid.UUID) -> None:
    """Corps réel du runner (Phase 1, inchangé) + persistance du rapport.

    Appelé par la tâche Celery (une session dédiée : le worker n'a pas celle
    de la requête HTTP qui a créé le rapport `running`)."""
    try:
        report = await upgrade_all()
    except MigrationsLockedError as exc:
        async with get_control_sessionmaker()() as session:
            record = await session.get(MigrationReportRecord, report_id)
            if record is not None:
                record.status = MigrationRunStatus.FAILED
                record.error = str(exc)
                record.finished_at = _now()
                await session.commit()
        return

    async with get_control_sessionmaker()() as session:
        record = await session.get(MigrationReportRecord, report_id)
        if record is None:  # pragma: no cover — le rapport vient d'être créé
            return
        record.status = MigrationRunStatus.DONE
        record.summary = report.summary
        record.outcomes = [_outcome_to_dict(o) for o in report.outcomes]
        record.finished_at = _now()
        await session.commit()
        logger.info("admin_migrations_run_done", summary=report.summary)


async def get_last_report(session: AsyncSession) -> MigrationReportRecord | None:
    return await session.scalar(
        select(MigrationReportRecord).order_by(MigrationReportRecord.started_at.desc()).limit(1)
    )
