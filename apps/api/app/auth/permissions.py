"""RBAC (plan global §4) : permissions namespacées `core.*`, rôles en code.

Décision D6 Phase 2 : les trois rôles intégrés et leurs ensembles de
permissions vivent ici, typés et testables. `memberships.role` (texte) permettra
d'ajouter des rôles custom (Phase 7+) sans migration destructive. Les modules
métier ajouteront leurs permissions sous leur propre namespace (`module_x.*`).

`require_permission` est LA dépendance unique de vérification : composée de
`resolve_tenant` (qui exige déjà session + membership), elle ne laisse passer
que les rôles dont l'ensemble contient la permission demandée.
"""

from collections.abc import Callable, Coroutine
from typing import Annotated

from fastapi import Depends, HTTPException

from app.tenancy.context import TenantContext
from app.tenancy.deps import resolve_tenant

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
    }
)

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    ROLE_MEMBER: _MEMBER_PERMISSIONS,
    ROLE_ADMIN: _ADMIN_PERMISSIONS,
    ROLE_OWNER: _ADMIN_PERMISSIONS,  # les actions réservées aux owners sont des règles métier
}

ROLES: frozenset[str] = frozenset(ROLE_PERMISSIONS)


def validate_role(role: str) -> str:
    if role not in ROLES:
        msg = f"Rôle inconnu : {role!r} (attendu : {', '.join(sorted(ROLES))})"
        raise ValueError(msg)
    return role


def role_has_permission(role: str, permission: str) -> bool:
    return permission in ROLE_PERMISSIONS.get(role, frozenset())


def require_permission(
    permission: str,
) -> Callable[..., Coroutine[None, None, TenantContext]]:
    """Fabrique de dépendance : 403 si le rôle du membership ne porte pas la permission."""
    if permission not in PERMISSIONS:
        msg = f"Permission inconnue du registre : {permission!r}"
        raise ValueError(msg)

    async def dependency(
        ctx: Annotated[TenantContext, Depends(resolve_tenant)],
    ) -> TenantContext:
        if ctx.role is None or not role_has_permission(ctx.role, permission):
            raise HTTPException(status_code=403, detail="Permission refusée")
        return ctx

    return dependency
