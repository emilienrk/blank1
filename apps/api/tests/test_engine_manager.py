import uuid
from unittest.mock import patch

from app.core.config import Settings
from app.tenancy.context import TenantContext
from app.tenancy.engine_manager import TenantEngineManager
from app.tenancy.models import TenantState


def _ctx(slug: str) -> TenantContext:
    return TenantContext(
        tenant_id=uuid.uuid4(),
        slug=slug,
        state=TenantState.ACTIVE,
        db_name=f"tenant_{slug}",
        db_host="default",
    )


def _manager(cache_size: int = 2) -> TenantEngineManager:
    # Les engines sont créés paresseusement SANS connexion : pas de Postgres requis ici.
    settings = Settings(tenant_engine_cache_size=cache_size, tenant_engine_pool_size=1)
    return TenantEngineManager(settings)


async def test_same_tenant_reuses_engine() -> None:
    manager = _manager()
    ctx = _ctx("acme")
    first = await manager.engine_for(ctx)
    second = await manager.engine_for(ctx)
    assert first is second
    await manager.dispose_all()


async def test_two_tenants_never_share_an_engine() -> None:
    manager = _manager()
    acme = await manager.engine_for(_ctx("acme"))
    globex = await manager.engine_for(_ctx("globex"))
    assert acme is not globex
    await manager.dispose_all()


async def test_lru_eviction_disposes_oldest_engine() -> None:
    manager = _manager(cache_size=2)
    ctx_a, ctx_b, ctx_c = _ctx("a"), _ctx("b"), _ctx("c")
    engine_a = await manager.engine_for(ctx_a)
    await manager.engine_for(ctx_b)
    # `a` redevient le plus récent : c'est `b` qui doit être évincé ensuite.
    await manager.engine_for(ctx_a)

    with patch.object(type(engine_a), "dispose", autospec=True, side_effect=None) as dispose:
        await manager.engine_for(ctx_c)
    assert dispose.await_count == 1

    cached = manager.cached_tenant_ids
    assert ctx_b.tenant_id not in cached
    assert set(cached) == {ctx_a.tenant_id, ctx_c.tenant_id}
    await manager.dispose_all()


async def test_evicted_tenant_gets_fresh_engine() -> None:
    manager = _manager(cache_size=1)
    ctx_a, ctx_b = _ctx("a"), _ctx("b")
    first = await manager.engine_for(ctx_a)
    await manager.engine_for(ctx_b)  # évince a
    second = await manager.engine_for(ctx_a)  # recréé paresseusement
    assert first is not second
    await manager.dispose_all()


async def test_invalidate_removes_engine() -> None:
    manager = _manager()
    ctx = _ctx("acme")
    first = await manager.engine_for(ctx)
    await manager.invalidate(ctx.tenant_id)
    assert ctx.tenant_id not in manager.cached_tenant_ids
    second = await manager.engine_for(ctx)
    assert first is not second
    await manager.dispose_all()
