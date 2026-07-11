"""Couche DB control-plane : engine async unique, sessions, Base déclarative.

Le control-plane porte le catalogue des tenants, les identités globales et les
memberships — jamais de données métier (invariant I3 Phase 1). Les données
métier vivent dans les DB tenant, accessibles uniquement via
`app.tenancy.session.get_tenant_session`.
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


class ControlPlaneBase(DeclarativeBase):
    """Base déclarative du schéma control-plane (MetaData séparée du schéma tenant)."""


_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_control_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(get_settings().control_plane_url, pool_pre_ping=True)
    return _engine


def get_control_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(get_control_engine(), expire_on_commit=False)
    return _sessionmaker


async def get_control_session() -> AsyncIterator[AsyncSession]:
    """Dépendance FastAPI : session control-plane."""
    async with get_control_sessionmaker()() as session:
        yield session


async def dispose_control_engine() -> None:
    """Ferme l'engine control-plane (tests, arrêt propre)."""
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None
