"""Résolution HTTP du tenant : sous-domaine x session x membership.

Phase 2 (T7) : le TODO Phase 1 est levé — la dépendance croise désormais le
tenant du sous-domaine avec la session utilisateur et le membership.
L'invariant racine n°1 est complet : contexte tenant = sous-domaine x session
x membership. 404 si tenant inconnu/non opérationnel, 403 si suspendu,
401 si non authentifié, 403 si authentifié mais non membre.
"""

from collections.abc import AsyncIterator
from typing import Annotated

import structlog
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import CurrentAuth, current_auth_or_none
from app.core.db import get_control_session
from app.directory.models import Membership
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
    auth: Annotated[CurrentAuth | None, Depends(current_auth_or_none)],
) -> AsyncIterator[TenantContext]:
    """Sous-domaine → tenant du catalogue → membership → contexte tenant posé."""
    slug = extract_slug(request.headers.get("host", ""))
    if slug is None:
        raise HTTPException(status_code=404, detail="Tenant introuvable")

    tenant = await session.scalar(select(Tenant).where(Tenant.slug == slug))
    if tenant is None or tenant.deleted_at is not None:
        # Soft-delete (ADR 0002) : indistinguable d'un tenant inexistant.
        raise HTTPException(status_code=404, detail="Tenant introuvable")
    if tenant.state is TenantState.SUSPENDED:
        raise HTTPException(status_code=403, detail="Tenant suspendu")

    if auth is None:
        raise HTTPException(status_code=401, detail="Authentification requise")
    membership = await session.scalar(
        select(Membership).where(
            Membership.user_id == auth.user.id, Membership.tenant_id == tenant.id
        )
    )
    if membership is None:
        raise HTTPException(status_code=403, detail="Accès refusé à ce tenant")

    ctx = TenantContext(tenant_id=tenant.id, slug=tenant.slug, role=membership.role)
    token = push_tenant(ctx)
    structlog.contextvars.bind_contextvars(tenant=ctx.slug)
    try:
        yield ctx
    finally:
        structlog.contextvars.unbind_contextvars("tenant")
        pop_tenant(token)
