"""Accès aux tables métier scopées tenant — LE SEUL chemin autorisé (invariant n°1).

`get_tenant_session()` (dépendance FastAPI) et `tenant_session()` (context manager
CLI/tâches) exigent un contexte tenant posé : sans lui, `TenantContextError`.
Le filtrage effectif est assuré par les garde-fous de `app.tenancy.tenant_base`
(installés sur la classe Session) — la session rendue ici est une session
ordinaire du sessionmaker unique, le scoping est automatique.
"""

from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_control_sessionmaker
from app.tenancy.context import current_tenant
from app.tenancy.tenant_base import install_tenant_guards

install_tenant_guards()


async def get_tenant_session() -> AsyncIterator[AsyncSession]:
    """Session scopée au tenant COURANT (lève TenantContextError sans contexte)."""
    current_tenant()
    async with get_control_sessionmaker()() as session:
        yield session


@asynccontextmanager
async def tenant_session() -> AsyncGenerator[AsyncSession]:
    """Session scopée tenant hors HTTP (CLI, tâches Celery, provisioning) —
    même exigence de contexte que la dépendance FastAPI."""
    current_tenant()
    async with get_control_sessionmaker()() as session:
        yield session
