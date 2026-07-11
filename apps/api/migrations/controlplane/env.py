"""Environnement Alembic du schéma CONTROL-PLANE (recette async officielle)."""

import asyncio

from alembic import context
from sqlalchemy import Connection, pool
from sqlalchemy.ext.asyncio import create_async_engine

import app.auth.models
import app.directory.models
import app.tenancy.models
from app.core.config import get_settings
from app.core.db import ControlPlaneBase

config = context.config
# Référencer les modules de modèles les enregistre dans la MetaData (autogenerate).
_MODEL_MODULES = (app.auth.models, app.directory.models, app.tenancy.models)
target_metadata = ControlPlaneBase.metadata


def _database_url() -> str:
    # Le runner multi-bases injecte l'URL via les attributs ; sinon, config env.
    url: str | None = config.attributes.get("database_url")
    return url if url is not None else get_settings().control_plane_url


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
