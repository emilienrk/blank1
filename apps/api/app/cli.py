"""CLI d'administration `saas` (Phase 1 T8 — le back-office arrive en Phase 3).

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
app.add_typer(tenant_app, name="tenant")
app.add_typer(db_app, name="db")


@tenant_app.command("create")
def tenant_create(
    slug: Annotated[str, typer.Argument(help="Sous-domaine du tenant (^[a-z][a-z0-9-]{1,38}$).")],
    name: Annotated[str | None, typer.Option(help="Nom affiché (défaut : le slug).")] = None,
) -> None:
    """Provisionne un tenant : catalogue, CREATE DATABASE, migrations, seed."""
    try:
        tenant = run_async(provision_tenant(slug, name or slug))
    except (ValueError, ProvisioningError) as exc:
        typer.echo(f"ERREUR : {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Tenant {tenant.slug!r} actif (base {tenant.db_name}).")


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
