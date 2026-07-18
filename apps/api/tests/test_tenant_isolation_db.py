"""Garde-fou de l'invariant racine n°1 en single-DB (ADR 0001) — critère
d'acceptation de la conversion base-par-tenant → tenant_id.

Prouve, contre une vraie base :
- sans contexte tenant : SELECT et INSERT sur un modèle `TenantScoped` lèvent
  `TenantContextError` ;
- sous le contexte du tenant A : select/get/update/delete ne voient JAMAIS les
  lignes du tenant B ;
- l'INSERT est estampillé automatiquement du tenant courant, et un `tenant_id`
  incohérent est refusé ;
- l'unicité de `Team.name` est bien PAR tenant ;
- le lazy-load (Team → rien ici, on passe par une requête jointe) reste filtré.
"""

import pytest
from sqlalchemy import func, select, text

from app.core.config import Settings
from app.core.db import get_control_sessionmaker
from app.directory.tenant_models import Team
from app.tenancy.context import TenantContext, TenantContextError, tenant_context
from app.tenancy.models import Tenant
from app.tenancy.session import get_tenant_session, tenant_session
from tests.conftest import requires_postgres
from tests.helpers import add_catalog_tenant

pytestmark = requires_postgres


def _ctx(tenant: Tenant) -> TenantContext:
    return TenantContext(tenant_id=tenant.id, slug=tenant.slug)


async def _seed_two_tenants() -> tuple[Tenant, Tenant]:
    tenant_a = await add_catalog_tenant("acme")
    tenant_b = await add_catalog_tenant("globex")
    for tenant, team_name in ((tenant_a, "equipe-a"), (tenant_b, "equipe-b")):
        with tenant_context(_ctx(tenant)):
            async with tenant_session() as session:
                session.add(Team(name=team_name))
                await session.commit()
    return tenant_a, tenant_b


async def test_select_without_context_raises(db_env: Settings) -> None:
    # Même via une session « control » ordinaire : le garde-fou est sur la classe
    # Session, pas sur un chemin d'accès particulier.
    async with get_control_sessionmaker()() as session:
        with pytest.raises(TenantContextError):
            await session.scalars(select(Team))


async def test_insert_without_context_raises(db_env: Settings) -> None:
    await add_catalog_tenant("acme")
    async with get_control_sessionmaker()() as session:
        session.add(Team(name="orpheline"))
        with pytest.raises(TenantContextError):
            await session.flush()


async def test_get_tenant_session_without_context_raises(db_env: Settings) -> None:
    sessions = get_tenant_session()
    with pytest.raises(TenantContextError):
        await anext(sessions)


async def test_cross_tenant_reads_are_invisible(db_env: Settings) -> None:
    tenant_a, tenant_b = await _seed_two_tenants()

    with tenant_context(_ctx(tenant_a)):
        async with tenant_session() as session:
            teams = (await session.scalars(select(Team))).all()
            assert [t.name for t in teams] == ["equipe-a"]
            # Agrégat référençant la colonne mappée : filtré aussi.
            count = await session.scalar(select(func.count(Team.id)))
            assert count == 1
            # Agrégat où la table n'apparaît que via select_from : with_loader_criteria
            # ne peut pas s'appliquer — la requête est REFUSÉE (pas de fuite silencieuse).
            with pytest.raises(TenantContextError, match="select_from"):
                await session.scalar(select(func.count()).select_from(Team))

    with tenant_context(_ctx(tenant_b)):
        async with tenant_session() as session:
            teams = (await session.scalars(select(Team))).all()
            assert [t.name for t in teams] == ["equipe-b"]


async def test_session_get_is_scoped(db_env: Settings) -> None:
    tenant_a, tenant_b = await _seed_two_tenants()
    with tenant_context(_ctx(tenant_b)):
        async with tenant_session() as session:
            team_b = (await session.scalars(select(Team))).one()

    # session.get() de l'id du tenant B sous le contexte A : introuvable.
    with tenant_context(_ctx(tenant_a)):
        async with tenant_session() as session:
            assert await session.get(Team, team_b.id) is None


async def test_update_delete_do_not_cross_tenants(db_env: Settings) -> None:
    tenant_a, _tenant_b = await _seed_two_tenants()

    from sqlalchemy import delete, update

    with tenant_context(_ctx(tenant_a)):
        async with tenant_session() as session:
            # UPDATE/DELETE en masse sans prédicat : seul le tenant A est touché.
            await session.execute(update(Team).values(description="touché"))
            await session.execute(delete(Team))
            await session.commit()

    async with get_control_sessionmaker()() as session:
        rows = (await session.execute(text("SELECT name, description FROM teams"))).all()
    assert [(r[0], r[1]) for r in rows] == [("equipe-b", None)]


async def test_insert_is_stamped_with_current_tenant(db_env: Settings) -> None:
    tenant_a, _ = await _seed_two_tenants()
    async with get_control_sessionmaker()() as session:
        rows = (
            await session.execute(text("SELECT tenant_id FROM teams WHERE name = 'equipe-a'"))
        ).all()
    assert [str(r[0]) for r in rows] == [str(tenant_a.id)]


async def test_insert_with_foreign_tenant_id_refused(db_env: Settings) -> None:
    tenant_a, tenant_b = await _seed_two_tenants()
    with tenant_context(_ctx(tenant_a)):
        async with tenant_session() as session:
            session.add(Team(name="intruse", tenant_id=tenant_b.id))
            with pytest.raises(TenantContextError):
                await session.flush()


async def test_team_name_unique_per_tenant_only(db_env: Settings) -> None:
    tenant_a, tenant_b = await _seed_two_tenants()
    # Le même nom chez un AUTRE tenant passe (unicité (tenant_id, name), ADR 0001).
    with tenant_context(_ctx(tenant_b)):
        async with tenant_session() as session:
            session.add(Team(name="equipe-a"))
            await session.commit()
    # Chez le MÊME tenant : violation d'unicité.
    from sqlalchemy.exc import IntegrityError

    with tenant_context(_ctx(tenant_a)):
        async with tenant_session() as session:
            session.add(Team(name="equipe-a"))
            with pytest.raises(IntegrityError):
                await session.commit()
