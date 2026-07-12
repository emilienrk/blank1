"""Tâches Celery RGPD (Phase 4 T4/T5/T6) : export à la demande (dispatché par le
back-office) + purges beat (rétention quotidienne, exports expirés, effacements
arrivés à échéance).
"""

# Celery n'expose pas de types (voir app/worker.py).
# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUntypedFunctionDecorator=false
# pyright: reportUnknownVariableType=false, reportCallIssue=false

import asyncio

import structlog
from celery import shared_task
from sqlalchemy import select

from app.core.db import dispose_control_engine, get_control_sessionmaker
from app.gdpr import export as export_service
from app.gdpr import retention
from app.gdpr.erasure import execute_pending_erasures
from app.tenancy.context import TenantContext, tenant_context
from app.tenancy.engine_manager import dispose_engine_manager, get_engine_manager
from app.tenancy.models import Tenant, TenantState

logger = structlog.get_logger()


# --- Export à la demande (dispatché par le back-office, T4) ---


async def _run_export(slug: str) -> None:
    try:
        await export_service.run_export(slug)
    finally:
        # Pools asyncpg liés à leur event loop (cf. app/cli.py, app/auth/tasks.py).
        await dispose_control_engine()
        await dispose_engine_manager()


@shared_task(name="core.gdpr.export_tenant")
def export_tenant_task(slug: str) -> None:
    asyncio.run(_run_export(slug))


async def enqueue_export(slug: str) -> None:
    """Frontière de dispatch vers Celery — remplacée en test (pas de broker requis)."""
    export_tenant_task.delay(slug)


# --- Rétention (beat quotidien, T6) ---


async def _apply_retention_policies() -> dict[str, dict[str, int]]:
    async with get_control_sessionmaker()() as control_session:
        tenants = list(
            (
                await control_session.scalars(
                    select(Tenant).where(Tenant.state == TenantState.ACTIVE)
                )
            ).all()
        )

    manager = get_engine_manager()
    report: dict[str, dict[str, int]] = {}
    for tenant in tenants:
        ctx = TenantContext(
            tenant_id=tenant.id,
            slug=tenant.slug,
            state=tenant.state,
            db_name=tenant.db_name,
            db_host=tenant.db_host,
            role=None,
        )
        with tenant_context(ctx):
            structlog.contextvars.bind_contextvars(tenant=ctx.slug)
            try:
                async with manager.session(ctx) as session:
                    tenant_report = await retention.apply_tenant_policies(session)
            finally:
                structlog.contextvars.unbind_contextvars("tenant")
        report[tenant.slug] = tenant_report
        logger.info("gdpr_retention_applied", tenant=tenant.slug, **tenant_report)
    return report


async def _run_retention() -> None:
    try:
        await _apply_retention_policies()
    finally:
        await dispose_control_engine()
        await dispose_engine_manager()


@shared_task(name="core.gdpr.apply_retention_policies")
def apply_retention_policies_task() -> None:
    asyncio.run(_run_retention())


# --- Effacements arrivés à échéance (beat, T5) ---


async def _run_pending_erasures() -> int:
    try:
        return await execute_pending_erasures()
    finally:
        await dispose_control_engine()
        await dispose_engine_manager()


@shared_task(name="core.gdpr.execute_pending_erasures")
def execute_pending_erasures_task() -> int:
    return asyncio.run(_run_pending_erasures())


# --- Purge des exports expirés (beat, T4) ---


@shared_task(name="core.gdpr.purge_expired_exports")
def purge_expired_exports_task() -> int:
    return export_service.purge_expired_exports()
