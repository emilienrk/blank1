"""Export RGPD d'un tenant (plan global §7, Phase 4 T4).

« Export = dump base tenant + données control-plane associées » : `pg_dump -Fc`
de la DB tenant + extraction JSON du control-plane (catalogue, membres,
invitations en cours) + manifeste, le tout dans une archive tar chiffrée
(`KeyProvider` existant, décision D8) déposée sur un volume dédié à TTL court
(décision D5 : remis par l'opérateur, jamais de self-service tenant).
"""

import asyncio
import json
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import Invitation
from app.core.config import Settings, get_settings
from app.core.crypto import get_key_provider
from app.core.db import get_control_sessionmaker
from app.directory.models import Membership, User
from app.tenancy.models import Tenant

logger = structlog.get_logger()

EXPORT_PREFIX = "export_"
EXPORT_SUFFIX = ".tar.enc"


class GdprExportError(RuntimeError):
    """Échec de l'export RGPD (tenant inconnu, pg_dump en échec, export introuvable)."""


def _now() -> datetime:
    return datetime.now(UTC)


def _export_dir(settings: Settings) -> Path:
    path = Path(settings.gdpr_export_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _export_filename(slug: str, timestamp: datetime) -> str:
    stamp = timestamp.strftime("%Y%m%dT%H%M%SZ")
    return f"{EXPORT_PREFIX}{slug}_{stamp}{EXPORT_SUFFIX}"


async def _control_plane_extract(session: AsyncSession, tenant: Tenant) -> dict[str, object]:
    """Ligne catalogue + users membres (id/email/display_name) + memberships +
    invitations en cours — jamais de credential (invariant Phase 2 n°2)."""
    rows = await session.execute(
        select(User, Membership.role)
        .join(Membership, Membership.user_id == User.id)
        .where(Membership.tenant_id == tenant.id)
    )
    members = [
        {"id": str(user.id), "email": user.email, "display_name": user.display_name, "role": role}
        for user, role in rows.all()
    ]
    pending = await session.scalars(
        select(Invitation).where(
            Invitation.tenant_id == tenant.id, Invitation.accepted_at.is_(None)
        )
    )
    pending_invitations = [
        {
            "email": invitation.email,
            "role": invitation.role,
            "expires_at": invitation.expires_at.isoformat(),
        }
        for invitation in pending
    ]
    return {
        "tenant": {
            "id": str(tenant.id),
            "slug": tenant.slug,
            "name": tenant.name,
            "plan": tenant.plan,
        },
        "members": members,
        "pending_invitations": pending_invitations,
    }


def _run_pg_dump(database_url: str, output_path: Path) -> None:
    # asyncpg exige `postgresql+asyncpg://` ; pg_dump veut un DSN postgresql:// classique.
    dsn = database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    result = subprocess.run(
        ["pg_dump", "-Fc", "-f", str(output_path), dsn],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        msg = f"pg_dump en échec : {result.stderr[:500]}"
        raise GdprExportError(msg)


async def run_export(tenant_slug: str, *, settings: Settings | None = None) -> Path:
    """Exporte un tenant de bout en bout ; retourne le chemin de l'archive chiffrée."""
    settings = settings or get_settings()
    async with get_control_sessionmaker()() as session:
        tenant = await session.scalar(select(Tenant).where(Tenant.slug == tenant_slug))
        if tenant is None:
            msg = f"Tenant {tenant_slug!r} inconnu au catalogue."
            raise GdprExportError(msg)
        extract = await _control_plane_extract(session, tenant)

    timestamp = _now()
    database_url = settings.tenant_database_url(tenant.db_name, tenant.db_host)

    with tempfile.TemporaryDirectory(prefix="gdpr-export-") as tmp:
        tmp_path = Path(tmp)
        dump_path = tmp_path / "tenant.dump"
        await asyncio.to_thread(_run_pg_dump, database_url, dump_path)

        manifest = {
            "tenant_slug": tenant.slug,
            "generated_at": timestamp.isoformat(),
            "contents": ["tenant.dump (pg_dump -Fc)", "control_plane.json", "manifest.json"],
        }
        (tmp_path / "manifest.json").write_text(json.dumps(manifest, indent=2))
        (tmp_path / "control_plane.json").write_text(json.dumps(extract, indent=2))

        tar_path = tmp_path / "archive.tar"
        with tarfile.open(tar_path, "w") as tar:
            tar.add(dump_path, arcname="tenant.dump")
            tar.add(tmp_path / "manifest.json", arcname="manifest.json")
            tar.add(tmp_path / "control_plane.json", arcname="control_plane.json")

        encrypted = get_key_provider().encrypt(tar_path.read_bytes())
        final_path = _export_dir(settings) / _export_filename(tenant.slug, timestamp)
        final_path.write_bytes(encrypted)

    logger.info("gdpr_export_done", tenant=tenant.slug, path=str(final_path))
    return final_path


@dataclass(slots=True)
class ExportFile:
    filename: str
    size_bytes: int
    created_at: datetime


def list_exports(tenant_slug: str, *, settings: Settings | None = None) -> list[ExportFile]:
    settings = settings or get_settings()
    prefix = f"{EXPORT_PREFIX}{tenant_slug}_"
    files = [
        ExportFile(
            filename=path.name,
            size_bytes=path.stat().st_size,
            created_at=datetime.fromtimestamp(path.stat().st_mtime, tz=UTC),
        )
        for path in _export_dir(settings).glob(f"{prefix}*{EXPORT_SUFFIX}")
    ]
    return sorted(files, key=lambda f: f.created_at, reverse=True)


def export_path(tenant_slug: str, filename: str, *, settings: Settings | None = None) -> Path:
    """Résout un export sur le disque — refuse tout ce qui sort du volume dédié ou
    n'appartient pas au tenant demandé (traversal, autre tenant)."""
    settings = settings or get_settings()
    directory = _export_dir(settings).resolve()
    has_prefix = filename.startswith(f"{EXPORT_PREFIX}{tenant_slug}_")
    if not has_prefix or "/" in filename or "\\" in filename:
        msg = "Export introuvable."
        raise GdprExportError(msg)
    path = (directory / filename).resolve()
    if path.parent != directory or not path.is_file():
        msg = "Export introuvable."
        raise GdprExportError(msg)
    return path


def purge_expired_exports(*, settings: Settings | None = None) -> int:
    """Supprime les exports au-delà de `gdpr_export_ttl_days` (tâche beat, T4)."""
    settings = settings or get_settings()
    cutoff = _now().timestamp() - settings.gdpr_export_ttl_days * 86400
    removed = 0
    for path in _export_dir(settings).glob(f"{EXPORT_PREFIX}*{EXPORT_SUFFIX}"):
        if path.stat().st_mtime < cutoff:
            path.unlink()
            removed += 1
    logger.info("gdpr_export_purge_done", removed=removed)
    return removed
