"""Montage des modules dans le cœur (Phase 7 T2) — appelé UNE fois (main.py/worker.py).

C'est la DERNIÈRE modification du cœur que les modules exigeront : ajouter un module
ensuite ne touche que `app/modules/<name>/` + une ligne au registre + une migration
tenant (invariant de phase n°1).

- `register_runtime()` : effets de bord idempotents partagés par l'API et le worker —
  rattachement des permissions aux rôles (T2), enregistrement des actions d'audit et
  des handlers d'événements connecteurs (Phase 5, D7).
- `mount_modules(app)` : monte chaque router sous `/api/v1/modules/{name}/…`, avec une
  dépendance `require_module_enabled(name)` sur TOUT le router (403 si le module n'est
  pas actif pour le tenant courant).

Les tâches périodiques sont déclarées au scheduler via `beat_entries()` au démarrage
du worker (voir `app.automation.scheduler`).
"""

from fastapi import Depends, FastAPI

from app.audit.service import register_module_actions, reset_module_actions
from app.auth.permissions import register_module_permission, reset_module_permissions
from app.automation.registry import MODULES, validate_registry
from app.automation.service import require_module_enabled
from app.connectors.webhooks import on_connector_event, reset_event_handlers


def register_runtime() -> None:
    """Rattache permissions/actions/handlers des modules au socle (idempotent)."""
    validate_registry(MODULES)
    reset_module_permissions()
    reset_module_actions()
    reset_event_handlers()
    for manifest in MODULES:
        for permission in manifest.permissions:
            register_module_permission(permission.name, permission.roles)
        register_module_actions(manifest.audit_actions)
        for subscription in manifest.connector_events:
            on_connector_event(subscription.capability, subscription.handler)


def mount_modules(app: FastAPI) -> None:
    """Monte les routers de modules sous `/api/v1/modules/{name}/…` (T2)."""
    register_runtime()
    for manifest in MODULES:
        app.include_router(
            manifest.router,
            prefix=f"/api/v1/modules/{manifest.name}",
            dependencies=[Depends(require_module_enabled(manifest.name))],
        )
