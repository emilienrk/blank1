"""Activation des modules par tenant (Phase 7 T3).

`enable_module` : vérifie l'existence au registre et que les `required_capabilities`
sont satisfaites par au moins une connexion ACTIVE du tenant (Phase 5) — sinon
erreur explicite listant ce qui manque ; audit `core.module.enabled`.
`disable_module` : coupe routes (via `require_module_enabled`) et tâches (le
scheduler filtre les tenants actifs, T4) ; les données du module en DB tenant
RESTENT (décision D6 : une désactivation peut être temporaire).
`require_module_enabled` : dépendance FastAPI (cache court par tenant) — 403 explicite
si le module n'est pas actif pour le tenant courant.
"""

import time
import uuid
from collections.abc import Callable, Coroutine
from typing import Annotated

import structlog
from fastapi import Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import record_audit_event_for_tenant
from app.automation.models import TenantModule
from app.automation.registry import get_module
from app.connectors.capabilities import CAPABILITY_NAMES, granted_capabilities
from app.connectors.tenant_models import ConnectionStatus, ConnectorConnection
from app.core.db import get_control_session, get_control_sessionmaker
from app.tenancy.context import TenantContext, tenant_context
from app.tenancy.deps import resolve_tenant
from app.tenancy.engine_manager import get_engine_manager
from app.tenancy.models import Tenant

logger = structlog.get_logger()

# Cache court de l'état d'activation par (tenant, module) : `require_module_enabled`
# est appelé à chaque requête d'une route de module. TTL borné + invalidation
# explicite à l'activation/désactivation (cohérence local ; cross-process borné par TTL).
_STATE_TTL_SECONDS = 10.0
_state_cache: dict[tuple[uuid.UUID, str], tuple[bool, float]] = {}


class ModuleError(RuntimeError):
    """Échec d'activation (module inconnu, capabilities manquantes)."""


def _capability_name(capability: type) -> str:
    name = CAPABILITY_NAMES.get(capability)
    if name is None:
        msg = f"Capability inconnue exigée par un module : {capability!r}"
        raise ModuleError(msg)
    return name


async def _tenant_granted_capabilities(tenant: Tenant) -> frozenset[str]:
    """Union des capabilities consenties par les connexions ACTIVES du tenant."""
    ctx = TenantContext(
        tenant_id=tenant.id,
        slug=tenant.slug,
        state=tenant.state,
        db_name=tenant.db_name,
        db_host=tenant.db_host,
        role=None,
    )
    granted: set[str] = set()
    with tenant_context(ctx):
        async with get_engine_manager().session(ctx) as session:
            connections = (
                await session.scalars(
                    select(ConnectorConnection).where(
                        ConnectorConnection.status == ConnectionStatus.ACTIVE
                    )
                )
            ).all()
            for connection in connections:
                granted |= granted_capabilities(connection)
    return frozenset(granted)


async def missing_capabilities(tenant: Tenant, module_name: str) -> list[str]:
    """Capabilities requises par le module et non satisfaites par le tenant."""
    manifest = get_module(module_name)
    if manifest is None:
        msg = f"Module inconnu du registre : {module_name!r}."
        raise ModuleError(msg)
    required = {_capability_name(cap) for cap in manifest.required_capabilities}
    if not required:
        return []
    granted = await _tenant_granted_capabilities(tenant)
    return sorted(required - granted)


def _invalidate(tenant_id: uuid.UUID, module_name: str) -> None:
    _state_cache.pop((tenant_id, module_name), None)


def reset_state_cache() -> None:
    """Réinitialise le cache d'état (tests)."""
    _state_cache.clear()


async def enable_module(
    control_session: AsyncSession,
    tenant: Tenant,
    module_name: str,
    *,
    actor_user_id: uuid.UUID | None = None,
    actor_label: str | None = None,
) -> TenantModule:
    """Active un module pour un tenant (T3). Refuse si une capability requise manque."""
    manifest = get_module(module_name)
    if manifest is None:
        msg = f"Module inconnu du registre : {module_name!r}."
        raise ModuleError(msg)

    missing = await missing_capabilities(tenant, module_name)
    if missing:
        msg = (
            f"Impossible d'activer {module_name!r} pour {tenant.slug!r} : capabilities "
            f"manquantes ({', '.join(missing)}). Connectez un compte les fournissant."
        )
        raise ModuleError(msg)

    row = await control_session.scalar(
        select(TenantModule).where(
            TenantModule.tenant_id == tenant.id, TenantModule.module_name == module_name
        )
    )
    if row is None:
        row = TenantModule(tenant_id=tenant.id, module_name=module_name, enabled=True)
        control_session.add(row)
    else:
        row.enabled = True

    # Audit AVANT le commit de l'action control-plane (règle Phase 4 pour les actions
    # control-plane : au pire un événement orphelin, jamais une action sans trace). La
    # trace vit en DB tenant (donnée du client) : deux bases physiques distinctes, pas
    # de transaction partagée.
    await record_audit_event_for_tenant(
        tenant,
        action="core.module.enabled",
        resource_type="module",
        resource_id=module_name,
        payload={"module": module_name, "version": manifest.version},
        actor_user_id=actor_user_id,
        actor_label=actor_label or "system",
    )
    await control_session.commit()
    await control_session.refresh(row)
    _invalidate(tenant.id, module_name)
    logger.info("module_enabled", tenant=tenant.slug, module=module_name)
    return row


async def disable_module(
    control_session: AsyncSession,
    tenant: Tenant,
    module_name: str,
    *,
    actor_user_id: uuid.UUID | None = None,
    actor_label: str | None = None,
) -> TenantModule | None:
    """Désactive un module (T3, D6) : coupe routes + tâches, conserve les données."""
    if get_module(module_name) is None:
        msg = f"Module inconnu du registre : {module_name!r}."
        raise ModuleError(msg)
    row = await control_session.scalar(
        select(TenantModule).where(
            TenantModule.tenant_id == tenant.id, TenantModule.module_name == module_name
        )
    )
    if row is None or not row.enabled:
        return row
    row.enabled = False
    await record_audit_event_for_tenant(
        tenant,
        action="core.module.disabled",
        resource_type="module",
        resource_id=module_name,
        payload={"module": module_name},
        actor_user_id=actor_user_id,
        actor_label=actor_label or "system",
    )
    await control_session.commit()
    await control_session.refresh(row)
    _invalidate(tenant.id, module_name)
    logger.info("module_disabled", tenant=tenant.slug, module=module_name)
    return row


async def is_module_enabled(
    control_session: AsyncSession, tenant_id: uuid.UUID, module_name: str
) -> bool:
    """État d'activation, avec cache court par (tenant, module)."""
    key = (tenant_id, module_name)
    cached = _state_cache.get(key)
    now = time.monotonic()
    if cached is not None and cached[1] > now:
        return cached[0]
    row = await control_session.scalar(
        select(TenantModule).where(
            TenantModule.tenant_id == tenant_id, TenantModule.module_name == module_name
        )
    )
    enabled = row is not None and row.enabled
    _state_cache[key] = (enabled, now + _STATE_TTL_SECONDS)
    return enabled


async def enabled_tenant_ids(module_name: str) -> list[uuid.UUID]:
    """Tenants où le module est actif (lu par le scheduler AVANT tout contexte, D4)."""
    async with get_control_sessionmaker()() as session:
        rows = await session.scalars(
            select(TenantModule.tenant_id).where(
                TenantModule.module_name == module_name, TenantModule.enabled.is_(True)
            )
        )
        return list(rows.all())


def require_module_enabled(
    module_name: str,
) -> Callable[..., Coroutine[None, None, TenantContext]]:
    """Fabrique de dépendance FastAPI injectée sur TOUT le router d'un module (T2) :
    403 explicite si le module n'est pas actif pour le tenant courant."""

    async def dependency(
        ctx: Annotated[TenantContext, Depends(resolve_tenant)],
        control_session: Annotated[AsyncSession, Depends(get_control_session)],
    ) -> TenantContext:
        if not await is_module_enabled(control_session, ctx.tenant_id, module_name):
            raise HTTPException(
                status_code=403, detail=f"Module « {module_name} » non activé pour ce tenant."
            )
        return ctx

    return dependency
