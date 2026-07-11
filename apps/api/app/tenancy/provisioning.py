"""Provisioning des tenants (plan global §3, Phase 1 T7).

Séquence : valider le slug → catalogue en `provisioning` → CREATE DATABASE →
migrations tenant → seed → `active`. Sur échec : `failed` + erreur loggée ;
`retry_provision` droppe la DB orpheline et rejoue.

Invariant I6 : CREATE/DROP DATABASE sont les seuls endroits où un identifiant
est interpolé — toujours après validation regex stricte + quoting.
TODO Phase 2 : invitation du premier owner à la fin du provisioning.
"""

import asyncio
import re
import uuid

import structlog
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import Settings, get_settings
from app.core.db import get_control_sessionmaker
from app.tenancy.migrations_runner import upgrade_database_sync
from app.tenancy.models import Tenant, TenantState, db_name_for_slug, validate_slug
from app.tenancy.tenant_base import TenantSetting

logger = structlog.get_logger()

DB_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,62}$")


class ProvisioningError(RuntimeError):
    """Échec de provisioning (le tenant est passé à l'état `failed`)."""


def _quoted_db_name(db_name: str) -> str:
    if not DB_NAME_RE.fullmatch(db_name):
        msg = f"Nom de base invalide : {db_name!r}"
        raise ValueError(msg)
    return f'"{db_name}"'


async def _admin_execute(sql: str, settings: Settings) -> None:
    """Exécute un ordre administratif (CREATE/DROP DATABASE) en autocommit."""
    engine = create_async_engine(
        settings.control_plane_url, poolclass=NullPool, isolation_level="AUTOCOMMIT"
    )
    try:
        async with engine.connect() as connection:
            await connection.execute(text(sql))
    finally:
        await engine.dispose()


async def create_database(db_name: str, settings: Settings) -> None:
    await _admin_execute(f"CREATE DATABASE {_quoted_db_name(db_name)}", settings)


async def drop_database_if_exists(db_name: str, settings: Settings) -> None:
    await _admin_execute(
        f"DROP DATABASE IF EXISTS {_quoted_db_name(db_name)} WITH (FORCE)", settings
    )


async def _seed_tenant_database(database_url: str, slug: str) -> None:
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with engine.begin() as connection:
            statement = (
                pg_insert(TenantSetting)
                .values(key="tenant:slug", value=slug)
                .on_conflict_do_update(index_elements=["key"], set_={"value": slug})
            )
            await connection.execute(statement)
    finally:
        await engine.dispose()


async def _set_tenant_state(tenant_id: uuid.UUID, state: TenantState) -> None:
    async with get_control_sessionmaker()() as session:
        tenant = await session.get(Tenant, tenant_id)
        if tenant is None:  # pragma: no cover — le tenant vient d'être créé
            msg = f"Tenant {tenant_id} introuvable au catalogue"
            raise ProvisioningError(msg)
        tenant.state = state
        await session.commit()


async def _run_provisioning_steps(tenant: Tenant, settings: Settings) -> None:
    await create_database(tenant.db_name, settings)
    database_url = settings.tenant_database_url(tenant.db_name, tenant.db_host)
    await asyncio.to_thread(upgrade_database_sync, "tenant", database_url)
    await _seed_tenant_database(database_url, tenant.slug)


async def provision_tenant(slug: str, name: str, *, settings: Settings | None = None) -> Tenant:
    """Crée un tenant de bout en bout ; état final `active`, ou `failed` + exception."""
    settings = settings or get_settings()
    validate_slug(slug)
    db_name = db_name_for_slug(slug, settings.tenant_db_prefix)

    async with get_control_sessionmaker()() as session:
        existing = await session.scalar(select(Tenant).where(Tenant.slug == slug))
        if existing is not None:
            msg = f"Le slug {slug!r} est déjà utilisé (état : {existing.state})."
            raise ProvisioningError(msg)
        tenant = Tenant(slug=slug, name=name, db_name=db_name)
        session.add(tenant)
        await session.commit()
        await session.refresh(tenant)

    logger.info("tenant_provisioning_started", tenant=slug, database=db_name)
    try:
        await _run_provisioning_steps(tenant, settings)
    except Exception as exc:
        await _set_tenant_state(tenant.id, TenantState.FAILED)
        error = f"{type(exc).__name__}: {exc}"[:500]
        logger.error("tenant_provisioning_failed", tenant=slug, error=error)
        msg = f"Provisioning de {slug!r} en échec : {error}"
        raise ProvisioningError(msg) from exc

    await _set_tenant_state(tenant.id, TenantState.ACTIVE)
    tenant.state = TenantState.ACTIVE
    logger.info("tenant_provisioned", tenant=slug, database=db_name)
    return tenant


async def retry_provision(slug: str, *, settings: Settings | None = None) -> Tenant:
    """Rejoue le provisioning d'un tenant en échec : droppe la DB orpheline puis recrée."""
    settings = settings or get_settings()

    async with get_control_sessionmaker()() as session:
        tenant = await session.scalar(select(Tenant).where(Tenant.slug == slug))
        if tenant is None:
            msg = f"Tenant {slug!r} inconnu au catalogue."
            raise ProvisioningError(msg)
        if tenant.state not in (TenantState.FAILED, TenantState.PROVISIONING):
            msg = (
                f"Le tenant {slug!r} est {tenant.state} — retry réservé aux provisionings en échec."
            )
            raise ProvisioningError(msg)
        tenant.state = TenantState.PROVISIONING
        await session.commit()
        await session.refresh(tenant)

    logger.info("tenant_provisioning_retry", tenant=slug, database=tenant.db_name)
    try:
        await drop_database_if_exists(tenant.db_name, settings)
        await _run_provisioning_steps(tenant, settings)
    except Exception as exc:
        await _set_tenant_state(tenant.id, TenantState.FAILED)
        error = f"{type(exc).__name__}: {exc}"[:500]
        logger.error("tenant_provisioning_failed", tenant=slug, error=error)
        msg = f"Retry du provisioning de {slug!r} en échec : {error}"
        raise ProvisioningError(msg) from exc

    await _set_tenant_state(tenant.id, TenantState.ACTIVE)
    tenant.state = TenantState.ACTIVE
    logger.info("tenant_provisioned", tenant=slug, database=tenant.db_name)
    return tenant
