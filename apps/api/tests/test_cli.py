"""Smoke des commandes CLI via CliRunner.

Les commandes font leur propre asyncio.run : on les invoque via asyncio.to_thread
depuis les tests async (le thread n'a pas de boucle en cours).
"""

import asyncio

from sqlalchemy import select
from typer.testing import CliRunner

from app.cli import app
from app.core.config import Settings
from app.core.db import get_control_sessionmaker
from app.tenancy.models import Tenant, TenantState
from tests.conftest import requires_postgres
from tests.helpers import reset_db_engines

pytestmark = requires_postgres

runner = CliRunner()


async def _invoke(*args: str, input_text: str | None = None) -> tuple[int, str]:
    result = await asyncio.to_thread(runner.invoke, app, list(args), input=input_text)
    return result.exit_code, result.output


async def test_cli_tenant_create_list_upgrade(db_env: Settings) -> None:
    code, output = await _invoke("tenant", "create", "acme", "--name", "ACME Corp")
    assert code == 0, output
    assert "actif" in output

    code, output = await _invoke("tenant", "list")
    assert code == 0
    assert "acme" in output
    assert "active" in output

    code, output = await _invoke("db", "upgrade")
    assert code == 0, output
    assert "Base migrée" in output


async def test_cli_create_duplicate_fails(db_env: Settings) -> None:
    code, _ = await _invoke("tenant", "create", "acme")
    assert code == 0
    code, output = await _invoke("tenant", "create", "acme")
    assert code == 1
    assert "ERREUR" in output


async def test_cli_create_invalid_slug_fails(db_env: Settings) -> None:
    code, output = await _invoke("tenant", "create", "Bad_Slug")
    assert code == 1
    assert "ERREUR" in output


async def test_cli_tenant_create_with_owner_email_prints_accept_url(db_env: Settings) -> None:
    """Phase 2 T8 : le provisioning enchaîne l'invitation du premier owner ;
    l'URL d'acceptation est toujours retournée à l'appelant (décision D8)."""
    code, output = await _invoke("tenant", "create", "acme", "--owner-email", "alice@example.com")
    assert code == 0, output
    assert "Invitation owner créée" in output
    assert "/accept-invitation?token=" in output


async def test_cli_invitation_create(db_env: Settings) -> None:
    code, _ = await _invoke("tenant", "create", "acme")
    assert code == 0

    code, output = await _invoke(
        "invitation", "create", "acme", "bob@example.com", "--role", "member"
    )
    assert code == 0, output
    assert "/accept-invitation?token=" in output

    # Rôle inconnu ou tenant inconnu → erreur explicite.
    code, output = await _invoke(
        "invitation", "create", "acme", "carol@example.com", "--role", "superuser"
    )
    assert code == 1
    assert "ERREUR" in output
    code, output = await _invoke("invitation", "create", "nexiste-pas", "bob@example.com")
    assert code == 1
    assert "ERREUR" in output


async def test_cli_tenant_delete_soft_deletes_after_slug_confirmation(db_env: Settings) -> None:
    code, _ = await _invoke("tenant", "create", "acme")
    assert code == 0

    # Confirmation invalide (re-saisie erronée) → refus, tenant intact.
    code, output = await _invoke("tenant", "delete", "acme", input_text="pas-le-bon-slug\n")
    assert code == 1
    assert "Confirmation invalide" in output
    async with get_control_sessionmaker()() as session:
        tenant = await session.scalar(select(Tenant).where(Tenant.slug == "acme"))
        assert tenant is not None
        assert tenant.deleted_at is None
    await reset_db_engines()

    code, output = await _invoke("tenant", "delete", "acme", input_text="acme\n")
    assert code == 0, output
    assert "soft-delete" in output
    async with get_control_sessionmaker()() as session:
        tenant = await session.scalar(select(Tenant).where(Tenant.slug == "acme"))
        assert tenant is not None
        assert tenant.deleted_at is not None
        assert tenant.state is TenantState.ACTIVE  # l'état ne change pas, seul deleted_at
    await reset_db_engines()

    # Déjà supprimé → erreur explicite ; tenant inconnu → erreur aussi.
    code, output = await _invoke("tenant", "delete", "acme", input_text="acme\n")
    assert code == 1
    assert "déjà supprimé" in output
    code, output = await _invoke("tenant", "delete", "nexiste-pas", input_text="nexiste-pas\n")
    assert code == 1
    assert "ERREUR" in output
