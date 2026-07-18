"""Couche DB : engine async unique, sessions, Base déclarative unique.

Base unique + `tenant_id` (ADR 0001) : catalogue, identités et tables métier
vivent dans la même base PostgreSQL. Les tables métier portent le mixin
`app.tenancy.tenant_base.TenantScoped` et ne sont accessibles que via
`app.tenancy.session` (filtre `tenant_id` injecté automatiquement, invariant n°1).
Les noms `get_control_*` sont conservés pour limiter le churn : « control » désigne
les tables NON scopées (catalogue, users, memberships).
"""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings


class Base(DeclarativeBase):
    """Base déclarative unique — toutes les tables, scopées tenant ou non."""


_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_control_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    return _engine


def get_control_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(get_control_engine(), expire_on_commit=False)
    return _sessionmaker


async def get_control_session() -> AsyncIterator[AsyncSession]:
    """Dépendance FastAPI : session sur les tables non scopées (catalogue, users)."""
    async with get_control_sessionmaker()() as session:
        yield session


async def dispose_control_engine() -> None:
    """Ferme l'engine (tests, arrêt propre)."""
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None
