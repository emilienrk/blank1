import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import Settings
from app.tenancy.migrations_runner import (
    ADVISORY_LOCK_KEY,
    MigrationsLockedError,
    read_schema_revision,
    upgrade_all,
)
from app.tenancy.provisioning import provision_tenant
from tests.conftest import requires_postgres

pytestmark = requires_postgres


async def test_all_databases_migrated_to_head(db_env: Settings) -> None:
    await provision_tenant("acme", "ACME")
    await provision_tenant("globex", "Globex")

    report = await upgrade_all()

    assert not report.has_failures
    assert [o.target for o in report.outcomes] == ["controlplane", "acme", "globex"]
    assert all(o.revision is not None for o in report.outcomes)


async def test_partial_failure_does_not_block_other_databases(db_env: Settings) -> None:
    await provision_tenant("acme", "ACME")
    await provision_tenant("globex", "Globex")

    # Sabotage de globex : version de schéma inconnue → l'upgrade de CETTE base échoue.
    globex_url = db_env.tenant_database_url(f"{db_env.tenant_db_prefix}globex")
    engine = create_async_engine(globex_url, poolclass=NullPool)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("UPDATE alembic_version SET version_num = 'bogus'"))
    finally:
        await engine.dispose()

    report = await upgrade_all()

    by_target = {o.target: o for o in report.outcomes}
    assert report.has_failures
    assert by_target["globex"].ok is False
    assert by_target["globex"].error is not None and "bogus" in by_target["globex"].error
    # Les autres bases sont migrées quand même (invariant I5).
    assert by_target["controlplane"].ok is True
    assert by_target["acme"].ok is True
    acme_url = db_env.tenant_database_url(f"{db_env.tenant_db_prefix}acme")
    assert await read_schema_revision(acme_url) == "0001_tenant"


async def test_advisory_lock_already_held_fails_fast(db_env: Settings) -> None:
    # Un « autre runner » détient le verrou sur le control-plane.
    engine = create_async_engine(db_env.control_plane_url, poolclass=NullPool)
    try:
        async with engine.connect() as holder:
            acquired = await holder.scalar(
                text("SELECT pg_try_advisory_lock(:key)"), {"key": ADVISORY_LOCK_KEY}
            )
            assert acquired is True
            with pytest.raises(MigrationsLockedError):
                await upgrade_all()
            await holder.execute(
                text("SELECT pg_advisory_unlock(:key)"), {"key": ADVISORY_LOCK_KEY}
            )
    finally:
        await engine.dispose()

    # Verrou relâché → le runner repasse.
    report = await upgrade_all()
    assert not report.has_failures
