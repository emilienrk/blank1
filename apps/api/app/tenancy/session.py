"""Accès aux DB tenant — LE SEUL chemin autorisé (invariant I1 Phase 1).

`get_tenant_session()` exige un contexte tenant posé : sans lui, TenantContextError.
Toute requête métier passe par ici ; le control-plane a ses propres sessions
(`app.core.db.get_control_session`) et ne porte aucune donnée métier.
"""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from app.tenancy.context import current_tenant
from app.tenancy.engine_manager import get_engine_manager


async def get_tenant_session() -> AsyncIterator[AsyncSession]:
    """Session async sur la DB du tenant COURANT (lève TenantContextError sans contexte)."""
    ctx = current_tenant()
    manager = get_engine_manager()
    async with manager.session(ctx) as session:
        yield session
