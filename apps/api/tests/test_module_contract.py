# pyright: reportUnusedFunction=false
"""Contrat de module (Phase 7 T1/T2, D2) : validation fail-fast au montage.

Un manifeste valide est accepté ; nom en collision, permission hors namespace,
route sans `require_permission`, tâche mal nommée, action d'audit hors namespace →
refus (l'invariant racine n°9 devient structurel, pas conventionnel)."""

import uuid

import pytest
from fastapi import APIRouter, Depends

from app.auth.permissions import (
    ROLE_ADMIN,
    register_module_permission,
    require_permission,
    reset_module_permissions,
)
from app.automation.contract import (
    ModuleManifest,
    ModulePermission,
    PeriodicTaskSpec,
)
from app.automation.registry import (
    ModuleRegistryError,
    validate_manifest,
    validate_registry,
)


async def _noop(_: uuid.UUID) -> None:
    return None


def _protected_router(permission: str) -> APIRouter:
    router = APIRouter()

    @router.get("/thing")
    async def _thing(_ctx: object = Depends(require_permission(permission))) -> dict[str, str]:
        return {"ok": "yes"}

    return router


def _valid_manifest(name: str = "demo") -> ModuleManifest:
    return ModuleManifest(
        name=name,
        version="1.0.0",
        title="Demo",
        description="Module de test.",
        router=_protected_router(f"{name}.read"),
        permissions=(ModulePermission(name=f"{name}.read", roles=(ROLE_ADMIN,)),),
        periodic_tasks=(PeriodicTaskSpec(name=f"{name}.tick", schedule_seconds=60.0, fn=_noop),),
        audit_actions=(f"{name}.happened",),
    )


def test_valid_manifest_accepted() -> None:
    validate_manifest(_valid_manifest())


def test_name_collision_refused() -> None:
    a = _valid_manifest("demo")
    b = _valid_manifest("demo")
    with pytest.raises(ModuleRegistryError, match="collision"):
        validate_registry([a, b])


def test_invalid_module_name_refused() -> None:
    manifest = ModuleManifest(
        name="Bad-Name",
        version="1.0.0",
        title="x",
        description="x",
        router=_protected_router("bad.read"),
    )
    with pytest.raises(ModuleRegistryError, match="Nom de module"):
        validate_manifest(manifest)


def test_permission_out_of_namespace_refused() -> None:
    manifest = ModuleManifest(
        name="demo",
        version="1.0.0",
        title="x",
        description="x",
        router=_protected_router("demo.read"),
        permissions=(ModulePermission(name="other.read", roles=(ROLE_ADMIN,)),),
    )
    with pytest.raises(ModuleRegistryError, match="hors namespace"):
        validate_manifest(manifest)


def test_route_without_require_permission_refused() -> None:
    router = APIRouter()

    @router.get("/open")
    async def _open() -> dict[str, str]:
        return {"ok": "yes"}

    manifest = ModuleManifest(
        name="demo", version="1.0.0", title="x", description="x", router=router
    )
    with pytest.raises(ModuleRegistryError, match="require_permission"):
        validate_manifest(manifest)


def test_task_misnamed_refused() -> None:
    manifest = ModuleManifest(
        name="demo",
        version="1.0.0",
        title="x",
        description="x",
        router=_protected_router("demo.read"),
        periodic_tasks=(PeriodicTaskSpec(name="cron.tick", schedule_seconds=60.0, fn=_noop),),
    )
    with pytest.raises(ModuleRegistryError, match="tâche"):
        validate_manifest(manifest)


def test_audit_action_out_of_namespace_refused() -> None:
    manifest = ModuleManifest(
        name="demo",
        version="1.0.0",
        title="x",
        description="x",
        router=_protected_router("demo.read"),
        audit_actions=("core.member.removed",),
    )
    with pytest.raises(ModuleRegistryError, match="action d'audit"):
        validate_manifest(manifest)


def test_register_module_permission_rejects_core_namespace() -> None:
    try:
        with pytest.raises(ValueError, match="core"):
            register_module_permission("core.hack", (ROLE_ADMIN,))
    finally:
        reset_module_permissions()


def test_require_permission_rejects_core_typo() -> None:
    # Le garde-fou anti-typo du socle reste strict pour `core.*`.
    with pytest.raises(ValueError, match="Permission inconnue"):
        require_permission("core.does.not.exist")
