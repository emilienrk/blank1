"""Helpers de test Phase 2 : users, sessions, memberships, tenants de catalogue."""

import uuid

from app.auth.service import create_session, set_password
from app.core.db import dispose_control_engine, get_control_sessionmaker
from app.directory.models import Membership, User
from app.tenancy.models import Tenant, TenantState


async def reset_db_engines() -> None:
    """À appeler à CHAQUE changement de boucle d'événements (pytest ↔ TestClient) :
    les pools asyncpg sont liés à leur boucle (piège documenté au handoff)."""
    await dispose_control_engine()


async def create_user(email: str, password: str | None = None) -> User:
    async with get_control_sessionmaker()() as session:
        user = User(email=email)
        session.add(user)
        await session.flush()
        if password is not None:
            await set_password(session, user.id, password)
        await session.commit()
        return user


async def create_session_token(user_id: uuid.UUID) -> str:
    async with get_control_sessionmaker()() as session:
        token = await create_session(session, user_id)
        await session.commit()
        return token


async def add_membership(user_id: uuid.UUID, tenant_id: uuid.UUID, role: str) -> None:
    async with get_control_sessionmaker()() as session:
        session.add(Membership(user_id=user_id, tenant_id=tenant_id, role=role))
        await session.commit()


async def add_catalog_tenant(slug: str, state: TenantState = TenantState.ACTIVE) -> Tenant:
    """Enregistre un tenant au catalogue sans passer par le provisioning (ni audit)."""
    async with get_control_sessionmaker()() as session:
        tenant = Tenant(slug=slug, name=slug.upper(), state=state)
        session.add(tenant)
        await session.commit()
        return tenant
