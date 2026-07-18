"""Provisioning des tenants — trivial en single-DB (ADR 0001).

Créer un tenant = valider le slug, insérer la ligne au catalogue (état `active`
d'emblée : l'insert EST le provisioning) et auditer `core.tenant.provisioned`
dans la MÊME transaction — bénéfice direct de la base unique.
L'invitation du premier owner (Phase 2) est composée au niveau CLI :
`saas tenant create --owner-email` enchaîne provisioning puis invitation.
"""

import structlog
from sqlalchemy import select

from app.audit.service import CLI_ACTOR_LABEL, record_audit_event
from app.core.db import get_control_sessionmaker
from app.tenancy.context import TenantContext, tenant_context
from app.tenancy.models import Tenant, TenantState, validate_slug

logger = structlog.get_logger()


class ProvisioningError(RuntimeError):
    """Échec de provisioning (slug invalide ou déjà pris)."""


async def provision_tenant(slug: str, name: str) -> Tenant:
    """Crée un tenant actif ; audit dans la même transaction que l'insert."""
    validate_slug(slug)

    async with get_control_sessionmaker()() as session:
        existing = await session.scalar(select(Tenant).where(Tenant.slug == slug))
        if existing is not None:
            msg = f"Le slug {slug!r} est déjà utilisé (état : {existing.state})."
            raise ProvisioningError(msg)
        tenant = Tenant(slug=slug, name=name, state=TenantState.ACTIVE)
        session.add(tenant)
        await session.flush()
        # Premier événement de la vie du tenant (Phase 4 T2) — acteur `cli` : le
        # provisioning n'est déclenché que par la CLI. Même session, même transaction :
        # rollback de l'un = rollback de l'autre.
        with tenant_context(TenantContext(tenant_id=tenant.id, slug=slug)):
            await record_audit_event(
                session,
                action="core.tenant.provisioned",
                resource_type="tenant",
                resource_id=str(tenant.id),
                payload={"slug": slug, "name": name},
                actor_label=CLI_ACTOR_LABEL,
            )
        await session.commit()

    logger.info("tenant_provisioned", tenant=slug)
    return tenant
