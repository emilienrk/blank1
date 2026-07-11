"""Smoke des commandes CLI via CliRunner.

Les commandes font leur propre asyncio.run : on les invoque via asyncio.to_thread
depuis les tests async (le thread n'a pas de boucle en cours).
"""

import asyncio

from typer.testing import CliRunner

from app.cli import app
from app.core.config import Settings
from tests.conftest import requires_postgres

pytestmark = requires_postgres

runner = CliRunner()


async def _invoke(*args: str) -> tuple[int, str]:
    result = await asyncio.to_thread(runner.invoke, app, list(args))
    return result.exit_code, result.output


async def test_cli_tenant_create_list_upgrade(db_env: Settings) -> None:
    code, output = await _invoke("tenant", "create", "acme", "--name", "ACME Corp")
    assert code == 0, output
    assert "actif" in output

    code, output = await _invoke("tenant", "list")
    assert code == 0
    assert "acme" in output
    assert "active" in output
    assert "0001_tenant" in output

    code, output = await _invoke("db", "upgrade")
    assert code == 0, output
    assert "2/2 base(s) migrée(s)" in output


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
