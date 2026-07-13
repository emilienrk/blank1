"""Émission de l'audit — API d'écriture unique (Phase 4 T2, décision D1).

`record_audit_event` écrit via la session tenant COURANTE, dans la même
transaction que l'action auditée : impossible d'avoir l'action sans sa trace
ou l'inverse (rollback de l'appelant → rollback de l'événement). Réutilisée
par toutes les phases suivantes (connecteurs, modules métier).
"""

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.tenant_models import AuditEvent
from app.tenancy.context import TenantContext
from app.tenancy.engine_manager import get_engine_manager
from app.tenancy.models import Tenant

# Registre typé des actions connues (namespaces `core.*` pour le socle,
# `connector.*` pour la Phase 5). Les actions de modules (`<module>.…`) sont
# enregistrées dynamiquement via `register_module_actions` (Phase 7) — appelé par
# le montage du runtime, jamais par un module directement.
ACTIONS: frozenset[str] = frozenset(
    {
        "core.tenant.provisioned",
        "connector.connected",
        "connector.reconsent_required",
        "connector.revoked",
        "connector.subscription_renewal_failed",
        "connector.event_received",
        "core.member.invited",
        "core.member.invitation_revoked",
        "core.member.invitation_accepted",
        "core.member.role_changed",
        "core.member.removed",
        "core.team.created",
        "core.team.deleted",
        "core.team.member_added",
        "core.team.member_removed",
        "core.ai.policy_changed",
        "core.ai.quota_exceeded",
        # Gouvernance des modules (Phase 7 T3) : activation/désactivation par tenant.
        "core.module.enabled",
        "core.module.disabled",
    }
)

# Actions de modules enregistrées dynamiquement (Phase 7) — additif au registre du
# socle, jamais une modification de `ACTIONS`. Réinitialisable en test.
_module_actions: set[str] = set()

SYSTEM_ACTOR_LABEL = "system"
CLI_ACTOR_LABEL = "cli"


def register_module_actions(actions: "frozenset[str] | set[str] | tuple[str, ...]") -> None:
    """Enregistre les actions d'audit d'un module (T2). Toutes namespacées
    `<module>.…` (jamais `core.*`, réservé au socle)."""
    for action in actions:
        if action.startswith("core."):
            msg = f"Action de module interdite dans le namespace `core.*` : {action!r}"
            raise ValueError(msg)
        _module_actions.add(action)


def reset_module_actions() -> None:
    """Réinitialise les actions de modules (montage idempotent + tests)."""
    _module_actions.clear()


def _is_known_action(action: str) -> bool:
    return action in ACTIONS or action in _module_actions


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
    if not _is_known_action(action):
        msg = f"Action d'audit inconnue du registre : {action!r}"
        raise ValueError(msg)
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
    """Écrit un événement d'audit hors contexte HTTP tenant (invitation acceptée
    depuis la route publique, provisioning, tâches beat, §T2) : ouvre sa propre
    session tenant et la commit seule — l'action déclenchante vit en control-plane
    ou est déjà commitée, deux bases physiques distinctes ne peuvent pas partager
    une transaction (nuance à l'atomicité stricte de D1, limitée aux actions
    tenant-only comme les équipes)."""
    ctx = TenantContext(
        tenant_id=tenant.id,
        slug=tenant.slug,
        state=tenant.state,
        db_name=tenant.db_name,
        db_host=tenant.db_host,
        role=None,
    )
    async with get_engine_manager().session(ctx) as session:
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
