"""Environnement Alembic — arbre unique, Base unique (recette async officielle)."""

import asyncio

from alembic import context
from sqlalchemy import Connection, pool
from sqlalchemy.ext.asyncio import create_async_engine

import app.ai.models
import app.audit.tenant_models
import app.auth.models
import app.automation.models

# Importer le registre des modules (Phase 7) enregistre les tables de TOUS les
# modules dans la MetaData (autogenerate) — l'ajout d'un module ne touche pas
# cet env.py : sa ligne au registre suffit (invariant de phase n°1).
import app.automation.registry  # pyright: ignore[reportUnusedImport]
import app.connectors.models
import app.connectors.tenant_models
import app.directory.models
import app.directory.tenant_models
import app.tenancy.models
from app.core.config import get_settings
from app.core.db import Base

config = context.config
# Référencer les modules de modèles les enregistre dans la MetaData (autogenerate).
_MODEL_MODULES = (
    app.ai.models,
    app.audit.tenant_models,
    app.auth.models,
    app.automation.models,
    app.connectors.models,
    app.connectors.tenant_models,
    app.directory.models,
    app.directory.tenant_models,
    app.tenancy.models,
)
target_metadata = Base.metadata


def _database_url() -> str:
    # Le CLI/conftest injecte l'URL via les attributs ; sinon, config env.
    url: str | None = config.attributes.get("database_url")
    return url if url is not None else get_settings().database_url


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
