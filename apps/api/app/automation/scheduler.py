"""Scheduler des tâches périodiques de modules (Phase 7 T4, décision D4).

Beat STATIQUE par tâche de module (une entrée par `PeriodicTaskSpec`, générée au
démarrage du worker depuis le registre) qui, à chaque tick, itère les tenants où le
module est `enabled` et publie une tâche unitaire par tenant (fan-out — le pattern
éprouvé des tâches multi-tenants des Phases 4/5/6, pas de beat dynamique par tenant).

La tâche unitaire : verrou Valkey par (module, tâche, tenant) contre les
chevauchements, pose le contexte tenant, exécute la fonction du module, capture
l'échec (un tenant en échec ne bloque pas les autres — philosophie du runner
Phase 1), logge un rapport corrélé.
"""

# Celery n'expose pas de types (voir app/worker.py).
# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUntypedFunctionDecorator=false
# pyright: reportUnknownVariableType=false, reportCallIssue=false

import asyncio
import uuid

import structlog
from celery import shared_task

from app.automation.contract import PeriodicTaskSpec
from app.automation.registry import MODULES, get_module
from app.automation.service import enabled_tenant_ids
from app.connectors import throttle
from app.core.db import dispose_control_engine, get_control_sessionmaker
from app.tenancy.context import TenantContext, tenant_context
from app.tenancy.engine_manager import dispose_engine_manager
from app.tenancy.models import Tenant, TenantState

logger = structlog.get_logger()


def _find_task(module_name: str, task_name: str) -> PeriodicTaskSpec | None:
    manifest = get_module(module_name)
    if manifest is None:
        return None
    for task in manifest.periodic_tasks:
        if task.name == task_name:
            return task
    return None


async def _active_tenant(tenant_id: uuid.UUID) -> Tenant | None:
    async with get_control_sessionmaker()() as session:
        tenant = await session.get(Tenant, tenant_id)
        if tenant is None or tenant.state is not TenantState.ACTIVE:
            return None
        return tenant


async def _dispose_engines() -> None:
    # Pools asyncpg liés à leur event loop (cf. app/gdpr/tasks.py, connectors).
    await dispose_control_engine()
    await dispose_engine_manager()


def _lock_name(module_name: str, task_name: str, tenant_id: uuid.UUID) -> str:
    return f"module:{module_name}:{task_name}:{tenant_id}"


# --- Fan-out (une entrée beat par tâche de module) ---


async def run_periodic_fanout(module_name: str, task_name: str) -> int:
    """Publie une tâche unitaire pour chaque tenant où le module est actif (D4).

    Retourne le nombre de tâches publiées (les tenants non actifs sont ignorés — le
    filtrage précède tout contexte tenant, l'intérêt du control-plane, D3)."""
    if _find_task(module_name, task_name) is None:
        logger.warning("module_periodic_unknown", module=module_name, task=task_name)
        return 0
    tenant_ids = await enabled_tenant_ids(module_name)
    published = 0
    for tenant_id in tenant_ids:
        await enqueue_unit(module_name, task_name, tenant_id)
        published += 1
    if published:
        logger.info("module_periodic_fanout", module=module_name, task=task_name, tenants=published)
    return published


async def run_periodic_unit(module_name: str, task_name: str, tenant_id: uuid.UUID) -> bool:
    """Exécute une tâche de module pour UN tenant, sous verrou et contexte posé (T4).

    Retourne True si la fonction a tourné, False si sautée (verrou pris, tenant
    inactif, module désactivé entre-temps). Un échec est capturé et loggé — il
    n'affecte pas les autres tenants (isolation, invariant de phase n°4)."""
    task = _find_task(module_name, task_name)
    if task is None:
        return False
    tenant = await _active_tenant(tenant_id)
    if tenant is None:
        return False

    lock_name = _lock_name(module_name, task_name, tenant_id)
    token = await throttle.acquire_lock(lock_name, ttl_seconds=throttle.LOCK_TTL_SECONDS)
    if token is None:
        # Un tick précédent tourne encore (cadence < durée) : on saute proprement.
        logger.info("module_periodic_skipped_locked", module=module_name, task=task_name)
        return False

    ctx = TenantContext(
        tenant_id=tenant.id,
        slug=tenant.slug,
        state=tenant.state,
        db_name=tenant.db_name,
        db_host=tenant.db_host,
        role=None,
    )
    with tenant_context(ctx):
        structlog.contextvars.bind_contextvars(tenant=ctx.slug, module=module_name)
        try:
            await task.fn(tenant.id)
            logger.info("module_periodic_ran", module=module_name, task=task_name)
            return True
        except Exception as exc:
            # Un tenant en échec ne bloque jamais les autres (runner Phase 1).
            logger.warning(
                "module_periodic_failed",
                module=module_name,
                task=task_name,
                error=exc.__class__.__name__,
            )
            return False
        finally:
            structlog.contextvars.unbind_contextvars("tenant", "module")
            await throttle.release_lock(lock_name, token)


# --- Frontières de dispatch Celery (remplacées en test : pas de broker) ---


async def enqueue_fanout(module_name: str, task_name: str) -> None:
    periodic_fanout_task.delay(module_name, task_name)


async def enqueue_unit(module_name: str, task_name: str, tenant_id: uuid.UUID) -> None:
    periodic_unit_task.delay(module_name, task_name, str(tenant_id))


@shared_task(name="automation.periodic_fanout")
def periodic_fanout_task(module_name: str, task_name: str) -> None:
    async def run() -> None:
        try:
            await run_periodic_fanout(module_name, task_name)
        finally:
            await _dispose_engines()

    asyncio.run(run())


@shared_task(name="automation.periodic_unit")
def periodic_unit_task(module_name: str, task_name: str, tenant_id: str) -> None:
    async def run() -> None:
        try:
            await run_periodic_unit(module_name, task_name, uuid.UUID(tenant_id))
        finally:
            await _dispose_engines()

    asyncio.run(run())


# --- Génération des entrées beat statiques (appelée depuis app/worker.py) ---


def beat_entries() -> dict[str, dict[str, object]]:
    """Une entrée beat par tâche périodique déclarée au registre (D4). Le fan-out
    sur les tenants actifs se fait à chaque tick, pas via N entrées par tenant."""
    entries: dict[str, dict[str, object]] = {}
    for manifest in MODULES:
        for task in manifest.periodic_tasks:
            entries[f"module-{task.name}"] = {
                "task": "automation.periodic_fanout",
                "schedule": task.schedule_seconds,
                "args": (manifest.name, task.name),
            }
    return entries
