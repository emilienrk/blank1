"""CLI d'administration `saas`.

En conteneur : `docker compose run --rm api saas <commande>`.
En local : `uv run saas <commande>` (Postgres du Compose requis).
"""

import asyncio
from collections.abc import Coroutine
from typing import Annotated

import typer

from app.core.config import get_settings
from app.core.db import dispose_control_engine
from app.core.logging import configure_logging
from app.directory.service import DirectoryError
from app.tenancy.engine_manager import dispose_engine_manager
from app.tenancy.migrations_runner import (
    MigrationReport,
    MigrationsLockedError,
    read_schema_revision,
    upgrade_all,
)
from app.tenancy.models import Tenant
from app.tenancy.provisioning import ProvisioningError, provision_tenant, retry_provision


def run_async[T](coro: Coroutine[None, None, T]) -> T:
    """asyncio.run + fermeture des engines DANS la même boucle.

    Les pools asyncpg sont liés à leur event loop : sans cette fermeture, un
    second asyncio.run (autre commande, tests) réutiliserait des connexions
    mortes ('got result for unknown protocol state')."""

    async def wrapper() -> T:
        try:
            return await coro
        finally:
            await dispose_control_engine()
            await dispose_engine_manager()

    return asyncio.run(wrapper())


app = typer.Typer(no_args_is_help=True, help="Administration du socle SaaS multi-tenant.")
tenant_app = typer.Typer(no_args_is_help=True, help="Gestion des tenants.")
db_app = typer.Typer(no_args_is_help=True, help="Migrations de schéma (control-plane + tenants).")
invitation_app = typer.Typer(no_args_is_help=True, help="Invitations d'utilisateurs.")
app.add_typer(tenant_app, name="tenant")
app.add_typer(db_app, name="db")
app.add_typer(invitation_app, name="invitation")


async def _invite(slug: str, email: str, role: str) -> str:
    """Crée une invitation (contexte admin CLI) et retourne l'URL d'acceptation."""
    from sqlalchemy import select

    from app.core.db import get_control_sessionmaker
    from app.directory.service import accept_url_for, create_invitation

    async with get_control_sessionmaker()() as session:
        tenant = await session.scalar(select(Tenant).where(Tenant.slug == slug))
        if tenant is None:
            msg = f"Tenant {slug!r} inconnu au catalogue."
            raise DirectoryError(msg)
        _, token = await create_invitation(session, tenant.id, email, role)
        await session.commit()
    return accept_url_for(token)


@tenant_app.command("create")
def tenant_create(
    slug: Annotated[str, typer.Argument(help="Sous-domaine du tenant (^[a-z][a-z0-9-]{1,38}$).")],
    name: Annotated[str | None, typer.Option(help="Nom affiché (défaut : le slug).")] = None,
    owner_email: Annotated[
        str | None,
        typer.Option("--owner-email", help="Invite ce premier owner à la fin du provisioning."),
    ] = None,
) -> None:
    """Provisionne un tenant : catalogue, CREATE DATABASE, migrations, seed.

    Avec --owner-email : invitation owner créée à la fin, URL affichée en sortie
    (décision D8 Phase 2 — l'URL est toujours retournée à l'appelant).
    """
    try:
        tenant = run_async(provision_tenant(slug, name or slug))
    except (ValueError, ProvisioningError) as exc:
        typer.echo(f"ERREUR : {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Tenant {tenant.slug!r} actif (base {tenant.db_name}).")
    if owner_email is not None:
        try:
            accept_url = run_async(_invite(slug, owner_email, "owner"))
        except (ValueError, DirectoryError) as exc:
            typer.echo(f"ERREUR invitation : {exc}", err=True)
            raise typer.Exit(code=1) from exc
        typer.echo(f"Invitation owner créée — URL d'acceptation : {accept_url}")


@invitation_app.command("create")
def invitation_create(
    slug: Annotated[str, typer.Argument(help="Slug du tenant.")],
    email: Annotated[str, typer.Argument(help="Email de la personne invitée.")],
    role: Annotated[str, typer.Option(help="Rôle : owner, admin ou member.")] = "member",
) -> None:
    """Invite un utilisateur sur un tenant ; affiche l'URL d'acceptation."""
    try:
        accept_url = run_async(_invite(slug, email, role))
    except (ValueError, DirectoryError) as exc:
        typer.echo(f"ERREUR : {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Invitation créée — URL d'acceptation : {accept_url}")


@tenant_app.command("retry-provision")
def tenant_retry_provision(
    slug: Annotated[str, typer.Argument(help="Slug d'un tenant en échec de provisioning.")],
) -> None:
    """Rejoue un provisioning en échec (droppe la base orpheline puis recrée)."""
    try:
        tenant = run_async(retry_provision(slug))
    except ProvisioningError as exc:
        typer.echo(f"ERREUR : {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Tenant {tenant.slug!r} actif (base {tenant.db_name}).")


async def _soft_delete(slug: str) -> Tenant:
    from datetime import UTC, datetime

    from sqlalchemy import select

    from app.core.db import get_control_sessionmaker

    async with get_control_sessionmaker()() as session:
        tenant = await session.scalar(select(Tenant).where(Tenant.slug == slug))
        if tenant is None:
            msg = f"Tenant {slug!r} inconnu au catalogue."
            raise DirectoryError(msg)
        if tenant.deleted_at is not None:
            msg = f"Tenant {slug!r} déjà supprimé (le {tenant.deleted_at:%Y-%m-%d})."
            raise DirectoryError(msg)
        tenant.deleted_at = datetime.now(UTC)
        await session.commit()
        return tenant


@tenant_app.command("delete")
def tenant_delete(
    slug: Annotated[str, typer.Argument(help="Slug du tenant à supprimer (soft-delete).")],
) -> None:
    """Soft-delete (ADR 0002) — confirmation par re-saisie du slug. Le tenant devient
    inaccessible immédiatement (résolution HTTP, tâches beat, webhooks) mais ses
    données restent en base ; réversible en remettant `deleted_at` à NULL en SQL."""
    typer.echo(f"Cette opération va rendre le tenant {slug!r} inaccessible (soft-delete).")
    confirmation = typer.prompt(f"Re-saisissez {slug!r} pour confirmer")
    if confirmation != slug:
        typer.echo("Confirmation invalide — annulé.", err=True)
        raise typer.Exit(code=1)
    try:
        tenant = run_async(_soft_delete(slug))
    except DirectoryError as exc:
        typer.echo(f"ERREUR : {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(
        f"Tenant {tenant.slug!r} supprimé (soft-delete) — données conservées, "
        f"restauration possible en SQL (deleted_at = NULL)."
    )


async def _tenant_rows() -> list[tuple[Tenant, str]]:
    from sqlalchemy import select

    from app.core.db import get_control_sessionmaker

    settings = get_settings()
    async with get_control_sessionmaker()() as session:
        tenants = list((await session.scalars(select(Tenant).order_by(Tenant.slug))).all())
    rows: list[tuple[Tenant, str]] = []
    for tenant in tenants:
        url = settings.tenant_database_url(tenant.db_name, tenant.db_host)
        revision = await read_schema_revision(url)
        rows.append((tenant, revision or "?"))
    return rows


@tenant_app.command("list")
def tenant_list() -> None:
    """Liste les tenants : état + version de schéma effective par base."""
    rows = run_async(_tenant_rows())
    if not rows:
        typer.echo("Aucun tenant au catalogue.")
        return
    typer.echo(f"{'SLUG':<20} {'ÉTAT':<14} {'BASE':<30} RÉVISION")
    for tenant, revision in rows:
        typer.echo(f"{tenant.slug:<20} {tenant.state:<14} {tenant.db_name:<30} {revision}")


def _print_report(report: MigrationReport) -> None:
    for outcome in report.outcomes:
        status = "OK    " if outcome.ok else "ÉCHEC "
        detail = outcome.revision if outcome.ok else outcome.error
        typer.echo(f"{status} {outcome.target:<20} {outcome.database:<30} {detail}")
    typer.echo(report.summary)


@db_app.command("upgrade")
def db_upgrade(
    only_controlplane: Annotated[
        bool, typer.Option("--only-controlplane", help="Ne migrer que le control-plane.")
    ] = False,
) -> None:
    """Migre le control-plane puis toutes les bases tenant (rapport par base).

    Code de sortie : 0 si tout est à head, 1 au moindre échec, 2 si un autre
    runner détient déjà le verrou advisory.
    """
    try:
        report = run_async(upgrade_all(only_controlplane=only_controlplane))
    except MigrationsLockedError as exc:
        typer.echo(f"ERREUR : {exc}", err=True)
        raise typer.Exit(code=2) from exc
    _print_report(report)
    if report.has_failures:
        raise typer.Exit(code=1)


def main() -> None:
    configure_logging(get_settings().log_level)
    app()


if __name__ == "__main__":
    main()
