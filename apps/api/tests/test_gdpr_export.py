"""Export RGPD (Phase 4 T4) : archive chiffrée déchiffrable, manifeste cohérent,
dump restaurable, purge au-delà du TTL."""

import subprocess
import tarfile
import tempfile
import time
from pathlib import Path

import pytest

from app.core.config import Settings
from app.core.crypto import get_key_provider
from app.gdpr.export import GdprExportError, list_exports, purge_expired_exports, run_export
from app.tenancy.provisioning import provision_tenant
from tests.conftest import requires_postgres
from tests.helpers import add_membership, create_user

pytestmark = requires_postgres


def _pg_env(settings: Settings) -> dict[str, str]:
    return {"PGPASSWORD": settings.postgres_password}


async def test_export_unknown_tenant_rejected(db_env: Settings) -> None:
    with pytest.raises(GdprExportError, match="inconnu"):
        await run_export("nexiste-pas")


async def test_export_produces_decryptable_restorable_archive(
    db_env: Settings, tmp_path: Path
) -> None:
    db_env.gdpr_export_dir = str(tmp_path / "exports")

    tenant = await provision_tenant("acme", "ACME")
    member = await create_user("bob@example.com")
    await add_membership(member.id, tenant.id, "member")

    path = await run_export("acme", settings=db_env)
    assert path.exists()
    assert path.parent == tmp_path / "exports"

    decrypted = get_key_provider().decrypt(path.read_bytes())
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        tar_path = tmp_dir / "archive.tar"
        tar_path.write_bytes(decrypted)
        with tarfile.open(tar_path) as tar:
            tar.extractall(tmp_dir, filter="data")

        manifest = (tmp_dir / "manifest.json").read_text()
        assert '"tenant_slug": "acme"' in manifest
        control_plane = (tmp_dir / "control_plane.json").read_text()
        assert "bob@example.com" in control_plane

        # Le dump se restaure dans une base jetable (critère de fin de phase, §E).
        restore_db = "test_gdpr_restore_acme"
        env = _pg_env(db_env)
        subprocess.run(
            ["createdb", "-h", db_env.postgres_host, "-U", db_env.postgres_user, restore_db],
            check=True,
            env=env,
        )
        try:
            restored = subprocess.run(
                [
                    "pg_restore",
                    "-h",
                    db_env.postgres_host,
                    "-U",
                    db_env.postgres_user,
                    "-d",
                    restore_db,
                    str(tmp_dir / "tenant.dump"),
                ],
                capture_output=True,
                text=True,
                env=env,
            )
            assert restored.returncode == 0, restored.stderr
            check = subprocess.run(
                [
                    "psql",
                    "-h",
                    db_env.postgres_host,
                    "-U",
                    db_env.postgres_user,
                    "-d",
                    restore_db,
                    "-tAc",
                    "SELECT value FROM tenant_settings WHERE key = 'tenant:slug'",
                ],
                capture_output=True,
                text=True,
                env=env,
            )
            assert check.stdout.strip() == "acme"
        finally:
            subprocess.run(
                ["dropdb", "-h", db_env.postgres_host, "-U", db_env.postgres_user, restore_db],
                check=False,
                env=env,
            )

    exports = list_exports("acme", settings=db_env)
    assert [f.filename for f in exports] == [path.name]


async def test_purge_expired_exports_respects_ttl(db_env: Settings, tmp_path: Path) -> None:
    db_env.gdpr_export_dir = str(tmp_path / "exports")
    db_env.gdpr_export_ttl_days = 0

    await provision_tenant("acme", "ACME")
    path = await run_export("acme", settings=db_env)
    time.sleep(1.1)  # TTL de 0 jour : le fichier fraîchement créé doit déjà être "expiré"

    removed = purge_expired_exports(settings=db_env)
    assert removed == 1
    assert not path.exists()


async def test_list_exports_isolated_per_tenant(db_env: Settings, tmp_path: Path) -> None:
    db_env.gdpr_export_dir = str(tmp_path / "exports")
    await provision_tenant("acme", "ACME")
    await provision_tenant("globex", "Globex")

    await run_export("acme", settings=db_env)
    await run_export("globex", settings=db_env)

    acme_exports = list_exports("acme", settings=db_env)
    assert len(acme_exports) == 1
    assert acme_exports[0].filename.startswith("export_acme_")
