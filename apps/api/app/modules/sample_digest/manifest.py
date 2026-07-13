"""Manifeste du module `sample_digest` (Phase 7 T6) — le contrat rempli.

Permissions `sample_digest.read`/`sample_digest.manage` rattachées aux rôles ;
capability requise `MailCapability` (contrôlée à l'activation) ; tâche périodique
quotidienne ; abonnement aux événements mail ; action d'audit du module.
"""

from app.auth.permissions import ROLE_ADMIN, ROLE_MEMBER, ROLE_OWNER
from app.automation.contract import (
    ConnectorEventSpec,
    ModuleManifest,
    ModulePermission,
    PeriodicTaskSpec,
)
from app.connectors.capabilities import MailCapability
from app.connectors.registry import CAPABILITY_MAIL
from app.modules.sample_digest.router import PERM_MANAGE, PERM_READ, router
from app.modules.sample_digest.service import (
    DIGEST_ACTION,
    TASK_NAME,
    generate_digest_task,
    on_mail_event,
)

# Quotidien : le digest se raisonne à la journée (cohérent avec les 24 h de fenêtre).
_DAILY_SECONDS = 86_400.0

manifest = ModuleManifest(
    name="sample_digest",
    version="1.0.0",
    title="Digest d'exemple",
    description="Résumé quotidien des emails reçus, produit par le provider IA du tenant.",
    router=router,
    permissions=(
        ModulePermission(
            name=PERM_READ,
            roles=(ROLE_MEMBER, ROLE_ADMIN, ROLE_OWNER),
            description="Lire les digests générés.",
        ),
        ModulePermission(
            name=PERM_MANAGE,
            roles=(ROLE_ADMIN, ROLE_OWNER),
            description="Déclencher manuellement la génération d'un digest.",
        ),
    ),
    periodic_tasks=(
        PeriodicTaskSpec(name=TASK_NAME, schedule_seconds=_DAILY_SECONDS, fn=generate_digest_task),
    ),
    connector_events=(ConnectorEventSpec(capability=CAPABILITY_MAIL, handler=on_mail_event),),
    required_capabilities=(MailCapability,),
    audit_actions=(DIGEST_ACTION,),
)
