"""Runner de migrations multi-bases (plan global §3, Phase 1 T6).

Séquence : verrou advisory sur le control-plane (un seul runner à la fois,
non bloquant) → upgrade control-plane → itération SÉQUENTIELLE des bases
tenant (décision D7). L'échec d'une base ne bloque jamais les suivantes
(invariant I5) ; le rapport est structuré, loggé en JSON, et le code de
sortie est non nul au moindre échec.
"""

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import structlog
from alembic import command
from alembic.config import Config
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import Settings, get_settings
from app.tenancy.models import Tenant, TenantState

logger = structlog.get_logger()

# apps/api/migrations/
MIGRATIONS_DIR = Path(__file__).resolve().parent.parent.parent / "migrations"

# Clé arbitraire mais fixe : identifie « les migrations » dans pg_locks.
ADVISORY_LOCK_KEY = 715_001

MigrationTree = Literal["controlplane", "tenant"]


class MigrationsLockedError(RuntimeError):
    """Un autre runner détient déjà le verrou advisory."""


@dataclass(slots=True)
class MigrationOutcome:
    database: str
    target: str  # "controlplane" ou slug du tenant
    ok: bool
    revision: str | None = None
    error: str | None = None


@dataclass(slots=True)
class MigrationReport:
    outcomes: list[MigrationOutcome] = field(default_factory=list[MigrationOutcome])

    @property
    def has_failures(self) -> bool:
        return any(not outcome.ok for outcome in self.outcomes)

    @property
    def summary(self) -> str:
        ok = sum(1 for o in self.outcomes if o.ok)
        return f"{ok}/{len(self.outcomes)} base(s) migrée(s)"


def _alembic_config(tree: MigrationTree, database_url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR / tree))
    config.attributes["database_url"] = database_url
    return config


def upgrade_database_sync(tree: MigrationTree, database_url: str) -> None:
    """`alembic upgrade head` sur une base. Sync : à appeler via asyncio.to_thread
    depuis du code async (l'env.py async fait son propre asyncio.run)."""
    command.upgrade(_alembic_config(tree, database_url), "head")


async def read_schema_revision(database_url: str) -> str | None:
    """Version Alembic effective d'une base (None si table absente ou base injoignable)."""
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            result = await connection.execute(text("SELECT version_num FROM alembic_version"))
            row = result.first()
            return None if row is None else str(row[0])
    except Exception:
        return None
    finally:
        await engine.dispose()


async def _migrate_one(
    tree: MigrationTree, database_url: str, *, database: str, target: str
) -> MigrationOutcome:
    try:
        await asyncio.to_thread(upgrade_database_sync, tree, database_url)
        revision = await read_schema_revision(database_url)
        logger.info("migration_applied", database=database, target=target, revision=revision)
        return MigrationOutcome(database=database, target=target, ok=True, revision=revision)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"[:500]
        logger.error("migration_failed", database=database, target=target, error=error)
        return MigrationOutcome(database=database, target=target, ok=False, error=error)


async def _list_target_tenants(settings: Settings) -> list[Tenant]:
    engine = create_async_engine(settings.control_plane_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            result = await session.scalars(
                select(Tenant)
                .where(Tenant.state.in_([TenantState.ACTIVE, TenantState.PROVISIONING]))
                .order_by(Tenant.slug)
            )
            return list(result.all())
    finally:
        await engine.dispose()


async def upgrade_all(
    *, only_controlplane: bool = False, settings: Settings | None = None
) -> MigrationReport:
    """Upgrade control-plane + toutes les bases tenant, sous verrou advisory."""
    settings = settings or get_settings()
    report = MigrationReport()

    lock_engine = create_async_engine(settings.control_plane_url, poolclass=NullPool)
    try:
        async with lock_engine.connect() as lock_connection:
            acquired = await lock_connection.scalar(
                text("SELECT pg_try_advisory_lock(:key)"), {"key": ADVISORY_LOCK_KEY}
            )
            if not acquired:
                msg = "Un runner de migrations est déjà en cours (verrou advisory occupé)."
                raise MigrationsLockedError(msg)
            try:
                report.outcomes.append(
                    await _migrate_one(
                        "controlplane",
                        settings.control_plane_url,
                        database=settings.postgres_db,
                        target="controlplane",
                    )
                )
                if not only_controlplane:
                    for tenant in await _list_target_tenants(settings):
                        url = settings.tenant_database_url(tenant.db_name, tenant.db_host)
                        report.outcomes.append(
                            await _migrate_one(
                                "tenant", url, database=tenant.db_name, target=tenant.slug
                            )
                        )
            finally:
                await lock_connection.execute(
                    text("SELECT pg_advisory_unlock(:key)"), {"key": ADVISORY_LOCK_KEY}
                )
    finally:
        await lock_engine.dispose()

    logger.info(
        "migration_report",
        summary=report.summary,
        failures=[o.database for o in report.outcomes if not o.ok],
    )
    return report
