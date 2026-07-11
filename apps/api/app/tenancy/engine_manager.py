"""Routage des connexions par tenant (plan global §3).

Un async engine SQLAlchemy par tenant : création paresseuse, cache LRU avec
`dispose()` à l'éviction, pool réduit par engine — le plafond global de
connexions est cache_size x pool_size. Les URL sont composées depuis le
catalogue (db_name, db_host) + credentials env, jamais stockées (décision D3).
"""

import asyncio
import uuid
from collections import OrderedDict
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from app.core.config import Settings, get_settings
from app.tenancy.context import TenantContext


class TenantEngineManager:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._engines: OrderedDict[uuid.UUID, AsyncEngine] = OrderedDict()
        self._lock = asyncio.Lock()

    async def engine_for(self, ctx: TenantContext) -> AsyncEngine:
        """Engine du tenant : réutilisé si présent (LRU), créé paresseusement sinon."""
        evicted: AsyncEngine | None = None
        async with self._lock:
            engine = self._engines.get(ctx.tenant_id)
            if engine is not None:
                self._engines.move_to_end(ctx.tenant_id)
                return engine
            url = self._settings.tenant_database_url(ctx.db_name, ctx.db_host)
            engine = create_async_engine(
                url,
                pool_size=self._settings.tenant_engine_pool_size,
                max_overflow=0,
                pool_pre_ping=True,
            )
            self._engines[ctx.tenant_id] = engine
            if len(self._engines) > self._settings.tenant_engine_cache_size:
                _, evicted = self._engines.popitem(last=False)
        if evicted is not None:
            await evicted.dispose()
        return engine

    async def invalidate(self, tenant_id: uuid.UUID) -> None:
        """Ferme et oublie l'engine d'un tenant (suspension, suppression, changement d'hôte)."""
        async with self._lock:
            engine = self._engines.pop(tenant_id, None)
        if engine is not None:
            await engine.dispose()

    async def dispose_all(self) -> None:
        async with self._lock:
            engines = list(self._engines.values())
            self._engines.clear()
        for engine in engines:
            await engine.dispose()

    @property
    def cached_tenant_ids(self) -> list[uuid.UUID]:
        return list(self._engines.keys())

    @asynccontextmanager
    async def session(self, ctx: TenantContext) -> AsyncGenerator[AsyncSession]:
        engine = await self.engine_for(ctx)
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session


_manager: TenantEngineManager | None = None


def get_engine_manager() -> TenantEngineManager:
    global _manager
    if _manager is None:
        _manager = TenantEngineManager()
    return _manager


async def dispose_engine_manager() -> None:
    """Ferme tous les engines tenant (tests, arrêt propre)."""
    global _manager
    if _manager is not None:
        await _manager.dispose_all()
    _manager = None
