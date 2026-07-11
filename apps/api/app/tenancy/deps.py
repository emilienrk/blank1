"""Résolution HTTP du tenant (décision D5 Phase 1).

La dépendance existe et est testée, mais AUCUNE route métier publique ne
l'expose tant que l'auth n'existe pas (elle révélerait l'existence des tenants).
La Phase 2 y branchera le croisement session utilisateur x membership.
"""

from collections.abc import AsyncIterator
from typing import Annotated

import structlog
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_control_session
from app.tenancy.context import TenantContext, pop_tenant, push_tenant
from app.tenancy.models import SLUG_RE, Tenant, TenantState


def extract_slug(host_header: str) -> str | None:
    """Extrait le slug candidat du header Host : premier label du sous-domaine."""
    host = host_header.split(":", 1)[0].strip().lower()
    labels = host.split(".")
    if len(labels) < 2:
        return None
    candidate = labels[0]
    return candidate if SLUG_RE.fullmatch(candidate) else None


async def resolve_tenant(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_control_session)],
) -> AsyncIterator[TenantContext]:
    """Sous-domaine → tenant du catalogue → contexte tenant posé pour la requête.

    404 si inconnu ou non opérationnel, 403 si suspendu.
    TODO Phase 2 : croiser avec la session utilisateur et le membership.
    """
    slug = extract_slug(request.headers.get("host", ""))
    if slug is None:
        raise HTTPException(status_code=404, detail="Tenant introuvable")

    tenant = await session.scalar(select(Tenant).where(Tenant.slug == slug))
    if tenant is None or tenant.state in (TenantState.PROVISIONING, TenantState.FAILED):
        raise HTTPException(status_code=404, detail="Tenant introuvable")
    if tenant.state is TenantState.SUSPENDED:
        raise HTTPException(status_code=403, detail="Tenant suspendu")

    ctx = TenantContext(
        tenant_id=tenant.id,
        slug=tenant.slug,
        state=tenant.state,
        db_name=tenant.db_name,
        db_host=tenant.db_host,
    )
    token = push_tenant(ctx)
    structlog.contextvars.bind_contextvars(tenant=ctx.slug)
    try:
        yield ctx
    finally:
        structlog.contextvars.unbind_contextvars("tenant")
        pop_tenant(token)
