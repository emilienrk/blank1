"""Émission de l'audit — API d'écriture unique (Phase 4 T2, décision D1).

`record_audit_event` écrit via la session tenant COURANTE, dans la même
transaction que l'action auditée : impossible d'avoir l'action sans sa trace
ou l'inverse (rollback de l'appelant → rollback de l'événement). Le `tenant_id`
de l'événement est estampillé automatiquement par les garde-fous de session
(ADR 0001). Réutilisée par toutes les briques (connecteurs, modules métier).
"""

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.tenant_models import AuditEvent
from app.tenancy.context import TenantContext, tenant_context
from app.tenancy.models import Tenant
from app.tenancy.session import tenant_session

# L'action est une string namespacée par convention : `core.*` pour le socle,
# `connector.*` pour les connecteurs, `<module>.*` pour les modules (le registre
# des modules valide ce namespace au démarrage, `app.automation.registry`).
SYSTEM_ACTOR_LABEL = "system"
CLI_ACTOR_LABEL = "cli"


async def record_audit_event(
    session: AsyncSession,
    *,
    action: str,
    resource_type: str,
    resource_id: str,
    payload: dict[str, Any],
    actor_user_id: uuid.UUID | None = None,
    actor_label: str = SYSTEM_ACTOR_LABEL,
) -> AuditEvent:
    """Écrit un événement d'audit sur la session tenant courante (même transaction
    que l'action auditée — l'appelant est responsable du commit, décision D1)."""
    event = AuditEvent(
        actor_user_id=actor_user_id,
        actor_label=actor_label,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        payload=payload,
    )
    session.add(event)
    await session.flush()
    return event


async def record_audit_event_for_tenant(
    tenant: Tenant,
    *,
    action: str,
    resource_type: str,
    resource_id: str,
    payload: dict[str, Any],
    actor_user_id: uuid.UUID | None = None,
    actor_label: str = SYSTEM_ACTOR_LABEL,
) -> None:
    """Écrit un événement d'audit hors contexte tenant posé (route publique
    d'acceptation d'invitation, tâches beat) : pose le contexte du tenant visé,
    ouvre sa propre session et la commit seule."""
    ctx = TenantContext(tenant_id=tenant.id, slug=tenant.slug)
    with tenant_context(ctx):
        async with tenant_session() as session:
            await record_audit_event(
                session,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                payload=payload,
                actor_user_id=actor_user_id,
                actor_label=actor_label,
            )
            await session.commit()
