"""Migrations Alembic — arbre unique, base unique (ADR 0001).

`upgrade_database_sync` fait un `alembic upgrade head` sur la base ; sync,
à appeler via `asyncio.to_thread` depuis du code async (l'env.py async fait
son propre `asyncio.run`). Plus de verrou advisory ni de rapport multi-bases :
il n'y a qu'un upgrade.
"""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

# apps/api/migrations/
MIGRATIONS_DIR = Path(__file__).resolve().parent.parent.parent / "migrations"


def _alembic_config(database_url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.attributes["database_url"] = database_url
    return config


def upgrade_database_sync(database_url: str) -> None:
    """`alembic upgrade head` sur la base."""
    command.upgrade(_alembic_config(database_url), "head")


async def read_schema_revision(database_url: str) -> str | None:
    """Version Alembic effective (None si table absente ou base injoignable)."""
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            result = await connection.execute(text("SELECT version_num FROM alembic_version"))
            row = result.first()
            return None if row is None else str(row[0])
    except Exception:
        return None
    finally:
        await engine.dispose()
