# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportPrivateUsage=false, reportUnknownLambdaType=false
"""Scheduler des tâches de modules (Phase 7 T4, décision D4).

Le fan-out ne publie que pour les tenants où le module est actif ; un échec sur un
tenant n'affecte pas les autres ; le verrou anti-chevauchement saute proprement ; la
tâche unitaire s'exécute avec le contexte tenant posé (prouvé par une requête DB).
"""

import uuid

import pytest
from sqlalchemy import text

from app.automation import scheduler
from app.automation import service as module_service
from app.automation.contract import PeriodicFn, PeriodicTaskSpec
from app.connectors import throttle
from app.core.config import Settings
from app.tenancy.context import current_tenant
from app.tenancy.provisioning import provision_tenant
from app.tenancy.session import get_tenant_session
from tests.conftest import requires_postgres
from tests.connector_helpers import install_fake_valkey, reset_connector_throttle
from tests.helpers import reset_db_engines
from tests.module_helpers import enable_module_row

pytestmark = requires_postgres

MODULE = "sample_digest"
TASK = "sample_digest.daily_digest"


def _spec(fn: PeriodicFn) -> PeriodicTaskSpec:
    return PeriodicTaskSpec(name=TASK, schedule_seconds=60.0, fn=fn)


async def test_fanout_publishes_only_for_enabled_tenants(
    db_env: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    active = await provision_tenant("acme", "ACME")
    await provision_tenant("globex", "GLOBEX")  # module NON activé
    await enable_module_row(active, MODULE)
    await reset_db_engines()

    published: list[str] = []

    async def _fake_enqueue(module: str, task: str, tenant_id: uuid.UUID) -> None:
        published.append(str(tenant_id))

    monkeypatch.setattr(scheduler, "enqueue_unit", _fake_enqueue)
    count = await scheduler.run_periodic_fanout(MODULE, TASK)
    assert count == 1
    assert published == [str(active.id)]


async def test_unit_runs_with_tenant_context_posed(
    db_env: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = await provision_tenant("acme", "ACME")
    await reset_db_engines()
    install_fake_valkey(monkeypatch)

    seen: dict[str, object] = {}

    async def _fn(tenant_id: uuid.UUID) -> None:
        # Le contexte est posé (sinon get_tenant_session lèverait TenantContextError) :
        # une requête DB du module le prouve.
        seen["ctx_id"] = current_tenant().tenant_id
        seen["arg_id"] = tenant_id
        async for session in get_tenant_session():
            await session.execute(text("SELECT 1"))
            break

    monkeypatch.setattr(scheduler, "_find_task", lambda m, t: _spec(_fn))
    ran = await scheduler.run_periodic_unit(MODULE, TASK, tenant.id)
    reset_connector_throttle()

    assert ran is True
    assert seen["ctx_id"] == tenant.id
    assert seen["arg_id"] == tenant.id


async def test_unit_failure_is_isolated(db_env: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    tenant = await provision_tenant("acme", "ACME")
    await reset_db_engines()
    install_fake_valkey(monkeypatch)

    async def _boom(_tenant_id: uuid.UUID) -> None:
        raise RuntimeError("échec métier du tenant")

    monkeypatch.setattr(scheduler, "_find_task", lambda m, t: _spec(_boom))
    # L'échec est capturé (False), jamais propagé : les autres tenants ne sont pas bloqués.
    ran = await scheduler.run_periodic_unit(MODULE, TASK, tenant.id)
    reset_connector_throttle()
    assert ran is False


async def test_lock_prevents_overlap(db_env: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    tenant = await provision_tenant("acme", "ACME")
    await reset_db_engines()
    install_fake_valkey(monkeypatch)

    ran_flags: list[bool] = []

    async def _fn(_tenant_id: uuid.UUID) -> None:
        ran_flags.append(True)

    monkeypatch.setattr(scheduler, "_find_task", lambda m, t: _spec(_fn))

    # Un tick précédent détient déjà le verrou (module, tâche, tenant).
    lock_name = scheduler._lock_name(MODULE, TASK, tenant.id)
    token = await throttle.acquire_lock(lock_name)
    assert token is not None
    try:
        ran = await scheduler.run_periodic_unit(MODULE, TASK, tenant.id)
        assert ran is False
        assert ran_flags == []
    finally:
        await throttle.release_lock(lock_name, token)
        reset_connector_throttle()


async def test_beat_entries_generated_from_registry() -> None:
    entries = scheduler.beat_entries()
    key = f"module-{TASK}"
    assert key in entries
    assert entries[key]["task"] == "automation.periodic_fanout"
    assert entries[key]["args"] == (MODULE, TASK)
    # Nettoyage du cache d'état éventuellement pollué par d'autres tests.
    module_service.reset_state_cache()
