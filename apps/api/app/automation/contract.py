"""Le contrat de module — `ModuleManifest` (Phase 7 T1, plan global §9).

La définition formelle et FIGÉE de ce qu'est un module métier : ses routes
(toutes protégées par `require_permission`), ses permissions (et leur rattachement
aux rôles intégrés), ses tâches périodiques, ses abonnements aux événements de
connecteurs, et les capabilities qu'il exige pour être activé.

Un module programme contre CE contrat stable, jamais contre le cœur : tout ce
qu'il consomme (capabilities Phase 5, `AIGateway` Phase 6, `record_audit_event`
Phase 4, `get_tenant_session` Phase 1) existe déjà. Le contrat porte un `version`
dès maintenant : toute extension future (webhooks propres, paramètres par tenant)
sera une PR du cœur, versionnée ici (risque n°2 du plan).
"""

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from uuid import UUID

from fastapi import APIRouter

from app.connectors.webhooks import ConnectorEvent

# Slug de module : `^[a-z][a-z0-9_]{1,30}$` (T1). Sert de préfixe aux permissions,
# aux tâches et aux tables tenant du module.
MODULE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,30}$")

# Signature imposée d'une tâche périodique (T1) : reçoit le tenant courant (dont le
# contexte est déjà posé par le scheduler), n'a pas de valeur de retour utile.
PeriodicFn = Callable[[UUID], Awaitable[None]]

# Handler d'événement connecteur (Phase 5, hook `on_connector_event`).
ConnectorEventFn = Callable[[ConnectorEvent], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class ModulePermission:
    """Une permission du module et les rôles intégrés qui la portent (T1).

    `name` est namespacé `<module>.…` (le registre refuse `core.*` et tout autre
    préfixe — invariant de phase n°5). `roles` rattache la permission aux rôles
    `owner`/`admin`/`member` du socle (Phase 2)."""

    name: str
    roles: tuple[str, ...]
    description: str = ""


@dataclass(frozen=True, slots=True)
class PeriodicTaskSpec:
    """Une tâche périodique du module (T1/T4).

    `name` est namespacé `<module>.…` ; `schedule_seconds` est la cadence du beat
    statique (le fan-out sur les tenants actifs est géré par le scheduler, D4) ;
    `fn` respecte la signature `(tenant_id) -> None`, exécutée sous contexte tenant
    posé et verrou anti-chevauchement."""

    name: str
    schedule_seconds: float
    fn: PeriodicFn


@dataclass(frozen=True, slots=True)
class ConnectorEventSpec:
    """Un abonnement du module au hook connecteur (Phase 5, D7).

    `capability` est le nom d'une capability (`mail`, `calendar`) ; `handler` est
    appelé, contexte tenant posé, pour chaque événement normalisé de cette
    capability."""

    capability: str
    handler: ConnectorEventFn


@dataclass(frozen=True, slots=True)
class ModuleManifest:
    """Le manifeste complet d'un module (T1) — figé et introspectable.

    Toutes les routes du `router` DOIVENT porter une dépendance `require_permission`
    (vérifié au montage, D2) ; les `permissions` et les `periodic_tasks` DOIVENT être
    namespacés `<name>.` (vérifié au démarrage, D2). Les `required_capabilities`
    (types de capability, ex. `MailCapability`) sont contrôlées à l'activation par
    tenant (T3)."""

    name: str
    version: str
    title: str
    description: str
    router: APIRouter
    permissions: tuple[ModulePermission, ...] = ()
    periodic_tasks: tuple[PeriodicTaskSpec, ...] = ()
    connector_events: tuple[ConnectorEventSpec, ...] = ()
    required_capabilities: tuple[type, ...] = field(default_factory=tuple)
    # Actions d'audit émises par le module (`<name>.…`) — enregistrées au registre
    # d'audit au montage (l'audit refuse toute action inconnue, Phase 4).
    audit_actions: tuple[str, ...] = ()
