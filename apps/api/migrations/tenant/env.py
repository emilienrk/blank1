"""Environnement Alembic du schéma TENANT (appliqué à chaque base tenant)."""

import asyncio

from alembic import context
from sqlalchemy import Connection, pool
from sqlalchemy.ext.asyncio import create_async_engine

from app.tenancy.tenant_base import TenantBase

config = context.config
target_metadata = TenantBase.metadata


def _database_url() -> str:
    # Le runner/provisioning injecte TOUJOURS l'URL de la base tenant cible ;
    # il n'existe pas d'URL tenant « par défaut ».
    url: str | None = config.attributes.get("database_url")
    if url is None:
        msg = "URL de base tenant manquante (attribut 'database_url')."
        raise RuntimeError(msg)
    return url


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    engine = create_async_engine(_database_url(), poolclass=pool.NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    msg = "Mode offline non supporté : les migrations s'exécutent en ligne."
    raise RuntimeError(msg)

asyncio.run(run_async_migrations())
