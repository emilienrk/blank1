"""RBAC (plan global §4) : permissions namespacées `core.*`, rôles en code.

Décision D6 Phase 2 : les trois rôles intégrés et leurs ensembles de
permissions vivent ici, typés et testables. `memberships.role` (texte) permettra
d'ajouter des rôles custom (Phase 7+) sans migration destructive.

Les modules métier (Phase 7) ajoutent leurs permissions sous leur propre namespace
(`<module>.…`) via `register_module_permission` — appelé UNE fois par le montage du
runtime (`app.automation.mounting`), jamais par un module directement. Le socle
garde son registre `core.*` strict (garde-fou anti-typo au démarrage) ; les
permissions de modules sont validées par format et par le test structurel D7.

`require_permission` est LA dépendance unique de vérification : composée de
`resolve_tenant` (qui exige déjà session + membership), elle ne laisse passer
que les rôles dont l'ensemble contient la permission demandée.
"""

import re
from collections.abc import Callable, Coroutine, Iterable
from typing import Annotated

from fastapi import Depends, HTTPException

from app.tenancy.context import TenantContext
from app.tenancy.deps import resolve_tenant

# Format d'une permission de module : `<module>.<action>` (namespaces `<name>.…`,
# jamais `core.*` — invariant de phase n°5).
_MODULE_PERMISSION_RE = re.compile(r"^(?!core\.)[a-z][a-z0-9_]{1,30}\.[a-z][a-z0-9_.]+$")

ROLE_OWNER = "owner"
ROLE_ADMIN = "admin"
ROLE_MEMBER = "member"

PERMISSIONS: frozenset[str] = frozenset(
    {
        "core.members.read",
        "core.members.manage",
        "core.teams.read",
        "core.teams.manage",
        "core.tenant.settings",
        "core.audit.read",
        "core.connectors.read",
        "core.connectors.manage",
        "core.ai.use",
    }
)

_MEMBER_PERMISSIONS = frozenset({"core.members.read", "core.teams.read", "core.connectors.read"})
_ADMIN_PERMISSIONS = _MEMBER_PERMISSIONS | frozenset(
    {
        "core.members.manage",
        "core.teams.manage",
        "core.tenant.settings",
        "core.audit.read",
        "core.connectors.manage",
        # Route de test du gateway IA (Phase 6 T6) : owner/admin par défaut.
        "core.ai.use",
    }
)

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    ROLE_MEMBER: _MEMBER_PERMISSIONS,
    ROLE_ADMIN: _ADMIN_PERMISSIONS,
    ROLE_OWNER: _ADMIN_PERMISSIONS,  # les actions réservées aux owners sont des règles métier
}

ROLES: frozenset[str] = frozenset(ROLE_PERMISSIONS)

# Permissions de modules enregistrées dynamiquement (Phase 7). Séparées du registre
# `core.*` : leur rattachement aux rôles est additif, jamais une modification des
# ensembles du socle. Rempli par le montage du runtime, réinitialisable en test.
_module_permissions: set[str] = set()
_module_role_grants: dict[str, set[str]] = {role: set() for role in ROLE_PERMISSIONS}


def register_module_permission(name: str, roles: Iterable[str]) -> None:
    """Enregistre une permission de module et la rattache à des rôles intégrés (T2).

    Refuse tout ce qui n'est pas namespacé `<module>.…` (invariant de phase n°5 :
    `core.*` interdit aux modules) et tout rôle inconnu."""
    if not _MODULE_PERMISSION_RE.fullmatch(name):
        msg = (
            f"Permission de module invalide : {name!r} — attendu `<module>.<action>` "
            "en minuscules, jamais `core.*`."
        )
        raise ValueError(msg)
    _module_permissions.add(name)
    for role in roles:
        if role not in _module_role_grants:
            msg = f"Rôle inconnu pour la permission {name!r} : {role!r}"
            raise ValueError(msg)
        _module_role_grants[role].add(name)


def reset_module_permissions() -> None:
    """Réinitialise les permissions de modules (montage idempotent + tests)."""
    _module_permissions.clear()
    for grants in _module_role_grants.values():
        grants.clear()


def is_known_permission(permission: str) -> bool:
    return permission in PERMISSIONS or permission in _module_permissions


def validate_role(role: str) -> str:
    if role not in ROLES:
        msg = f"Rôle inconnu : {role!r} (attendu : {', '.join(sorted(ROLES))})"
        raise ValueError(msg)
    return role


def role_has_permission(role: str, permission: str) -> bool:
    if permission in ROLE_PERMISSIONS.get(role, frozenset()):
        return True
    return permission in _module_role_grants.get(role, set())


def require_permission(
    permission: str,
) -> Callable[..., Coroutine[None, None, TenantContext]]:
    """Fabrique de dépendance : 403 si le rôle du membership ne porte pas la permission.

    Les permissions `core.*` doivent figurer au registre du socle (garde-fou
    anti-typo). Les permissions de modules (`<module>.…`) sont validées par format :
    leur rattachement aux rôles est posé par le montage (T2), leur unicité et leur
    présence sur chaque route sont vérifiées au démarrage (D2)."""
    if permission.startswith("core."):
        if permission not in PERMISSIONS:
            msg = f"Permission inconnue du registre : {permission!r}"
            raise ValueError(msg)
    elif not _MODULE_PERMISSION_RE.fullmatch(permission):
        msg = f"Permission de module invalide : {permission!r}"
        raise ValueError(msg)

    async def dependency(
        ctx: Annotated[TenantContext, Depends(resolve_tenant)],
    ) -> TenantContext:
        if ctx.role is None or not role_has_permission(ctx.role, permission):
            raise HTTPException(status_code=403, detail="Permission refusée")
        return ctx

    # Exposé pour l'introspection du montage (D2) : retrouver la permission exigée
    # par une route sans ré-exécuter la dépendance.
    dependency.required_permission = permission  # type: ignore[attr-defined]
    return dependency
