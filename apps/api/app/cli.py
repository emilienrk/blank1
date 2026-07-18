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
from app.core.migrations import read_schema_revision, upgrade_database_sync
from app.directory.service import DirectoryError
from app.tenancy.models import Tenant
from app.tenancy.provisioning import ProvisioningError, provision_tenant


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

    return asyncio.run(wrapper())


app = typer.Typer(no_args_is_help=True, help="Administration du socle SaaS multi-tenant.")
tenant_app = typer.Typer(no_args_is_help=True, help="Gestion des tenants.")
db_app = typer.Typer(no_args_is_help=True, help="Migrations de schéma (base unique).")
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
    """Crée un tenant (insert au catalogue, actif d'emblée — ADR 0001).

    Avec --owner-email : invitation owner créée à la fin, URL affichée en sortie
    (décision D8 Phase 2 — l'URL est toujours retournée à l'appelant).
    """
    try:
        tenant = run_async(provision_tenant(slug, name or slug))
    except (ValueError, ProvisioningError) as exc:
        typer.echo(f"ERREUR : {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Tenant {tenant.slug!r} actif.")
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


async def _tenant_rows() -> list[Tenant]:
    from sqlalchemy import select

    from app.core.db import get_control_sessionmaker

    async with get_control_sessionmaker()() as session:
        return list((await session.scalars(select(Tenant).order_by(Tenant.slug))).all())


@tenant_app.command("list")
def tenant_list() -> None:
    """Liste les tenants du catalogue (état, soft-delete éventuel)."""
    rows = run_async(_tenant_rows())
    if not rows:
        typer.echo("Aucun tenant au catalogue.")
        return
    typer.echo(f"{'SLUG':<20} {'ÉTAT':<14} SUPPRIMÉ")
    for tenant in rows:
        deleted = f"{tenant.deleted_at:%Y-%m-%d}" if tenant.deleted_at else "-"
        typer.echo(f"{tenant.slug:<20} {tenant.state:<14} {deleted}")


@db_app.command("upgrade")
def db_upgrade() -> None:
    """`alembic upgrade head` sur la base unique (ADR 0001)."""
    settings = get_settings()

    async def _run() -> str | None:
        await asyncio.to_thread(upgrade_database_sync, settings.database_url)
        return await read_schema_revision(settings.database_url)

    try:
        revision = run_async(_run())
    except Exception as exc:
        typer.echo(f"ERREUR : {type(exc).__name__}: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Base migrée — révision {revision or '?'}.")


def main() -> None:
    configure_logging(get_settings().log_level)
    app()


if __name__ == "__main__":
    main()
