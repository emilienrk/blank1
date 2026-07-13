"""Registre des modules — l'UNIQUE point de couture cœur ↔ modules (Phase 7 T2).

Décision D1 : liste explicite en code (`MODULES`), remplie par import des manifests
depuis `app/modules/…`. Les modules sont développés par la même équipe dans le même
repo (monolithe modulaire §2) : la découverte dynamique (entry-points, scan de
dossiers) ajoute de la magie, casse pyright et l'analyse IA, pour un besoin de tiers
inexistant. **Ajouter un module = une ligne ici**, revue en PR.

Validations au démarrage (fail-fast, D2) : noms uniques, permissions toutes
préfixées `<name>.` (jamais `core.*`), chaque route du router porte une dépendance
`require_permission`, tâches nommées `<name>.…`. Un oubli devient une erreur de boot
en CI, pas une faille en prod.
"""

from collections.abc import Iterable

from fastapi.routing import APIRoute

from app.automation.contract import MODULE_NAME_RE, ModuleManifest

# --- Import des manifests de modules (D1 : une ligne par module) ---
from app.modules.sample_digest.manifest import manifest as sample_digest_manifest

MODULES: list[ModuleManifest] = [
    sample_digest_manifest,
]


class ModuleRegistryError(RuntimeError):
    """Manifeste invalide détecté au démarrage (fail-fast, D2)."""


def _route_permissions(route: APIRoute) -> set[str]:
    """Permissions exigées par une route, retrouvées par introspection de l'arbre de
    dépendances FastAPI (chaque `require_permission(...)` expose `required_permission`)."""
    found: set[str] = set()
    stack = list(route.dependant.dependencies)
    while stack:
        dependant = stack.pop()
        call = dependant.call
        permission = getattr(call, "required_permission", None)
        if isinstance(permission, str):
            found.add(permission)
        stack.extend(dependant.dependencies)
    return found


def validate_manifest(manifest: ModuleManifest) -> None:
    """Valide un manifeste isolément (format, cohérence des namespaces, D2)."""
    name = manifest.name
    if not MODULE_NAME_RE.fullmatch(name):
        msg = f"Nom de module invalide : {name!r} (attendu `^[a-z][a-z0-9_]{{1,30}}$`)."
        raise ModuleRegistryError(msg)

    prefix = f"{name}."
    for permission in manifest.permissions:
        if not permission.name.startswith(prefix):
            msg = (
                f"Module {name!r} : permission {permission.name!r} hors namespace "
                f"{prefix!r} (invariant de phase n°5)."
            )
            raise ModuleRegistryError(msg)

    for task in manifest.periodic_tasks:
        if not task.name.startswith(prefix):
            msg = f"Module {name!r} : tâche {task.name!r} hors namespace {prefix!r}."
            raise ModuleRegistryError(msg)

    for action in manifest.audit_actions:
        if not action.startswith(prefix):
            msg = f"Module {name!r} : action d'audit {action!r} hors namespace {prefix!r}."
            raise ModuleRegistryError(msg)

    for route in manifest.router.routes:
        if not isinstance(route, APIRoute):
            continue
        permissions = _route_permissions(route)
        if not any(p.startswith(prefix) for p in permissions):
            msg = (
                f"Module {name!r} : la route {route.path!r} ne porte aucune dépendance "
                f'`require_permission("{prefix}…")` (invariant de phase n°2, D2).'
            )
            raise ModuleRegistryError(msg)


def validate_registry(modules: Iterable[ModuleManifest]) -> None:
    """Valide l'ensemble du registre (unicité des noms + chaque manifeste)."""
    seen: set[str] = set()
    for manifest in modules:
        if manifest.name in seen:
            msg = f"Nom de module en collision au registre : {manifest.name!r}."
            raise ModuleRegistryError(msg)
        seen.add(manifest.name)
        validate_manifest(manifest)


def get_module(name: str) -> ModuleManifest | None:
    for manifest in MODULES:
        if manifest.name == name:
            return manifest
    return None


def module_names() -> list[str]:
    return [manifest.name for manifest in MODULES]


# Fail-fast à l'import du registre (donc au démarrage de l'API et du worker).
validate_registry(MODULES)
