# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
"""Activation des modules par tenant (Phase 7 T3).

Activation sans la capability requise → erreur explicite ; avec → activé + audit
`core.module.enabled` ; désactivation → module inactif (routes 403 via
`require_module_enabled`), non publié par le scheduler, données tenant intactes (D6).
"""

from sqlalchemy import select

from app.audit.tenant_models import AuditEvent
from app.automation import service as module_service
from app.automation.scheduler import enabled_tenant_ids
from app.connectors.tenant_models import ConnectorProvider
from app.core.config import Settings
from app.core.db import get_control_sessionmaker
from app.modules.sample_digest.tenant_models import SampleDigestDigest
from app.tenancy.context import tenant_context
from app.tenancy.provisioning import provision_tenant
from app.tenancy.session import tenant_session
from tests.conftest import requires_postgres
from tests.connector_helpers import create_connection, ctx_for
from tests.helpers import reset_db_engines

pytestmark = requires_postgres


async def test_enable_without_capability_lists_missing(db_env: Settings) -> None:
    tenant = await provision_tenant("acme", "ACME")
    await reset_db_engines()

    missing = await module_service.missing_capabilities(tenant, "sample_digest")
    assert missing == ["mail"]

    async with get_control_sessionmaker()() as session:
        try:
            await module_service.enable_module(session, tenant, "sample_digest")
            raise AssertionError("attendu : ModuleError")
        except module_service.ModuleError as exc:
            assert "mail" in str(exc)


async def test_enable_with_capability_activates_and_audits(db_env: Settings) -> None:
    tenant = await provision_tenant("acme", "ACME")
    # Connexion Google active → capability mail consentie.
    await create_connection(tenant, provider=ConnectorProvider.GOOGLE)
    await reset_db_engines()

    async with get_control_sessionmaker()() as session:
        row = await module_service.enable_module(session, tenant, "sample_digest")
        assert row.enabled is True

    async with get_control_sessionmaker()() as session:
        assert await module_service.is_module_enabled(session, tenant.id, "sample_digest") is True

    # Audit en DB tenant.
    with tenant_context(ctx_for(tenant)):
        async with tenant_session() as session:
            actions = [e.action for e in (await session.scalars(select(AuditEvent))).all()]
            assert "core.module.enabled" in actions


async def test_disable_keeps_tenant_data_and_stops_scheduling(db_env: Settings) -> None:
    tenant = await provision_tenant("acme", "ACME")
    await create_connection(tenant, provider=ConnectorProvider.GOOGLE)
    await reset_db_engines()

    # Un digest préexistant en DB tenant (donnée du module).
    with tenant_context(ctx_for(tenant)):
        async with tenant_session() as session:
            session.add(SampleDigestDigest(summary="ancien", message_count=1))
            await session.commit()

    async with get_control_sessionmaker()() as session:
        await module_service.enable_module(session, tenant, "sample_digest")
    assert await enabled_tenant_ids("sample_digest") == [tenant.id]

    async with get_control_sessionmaker()() as session:
        await module_service.disable_module(session, tenant, "sample_digest")

    # Plus publié par le scheduler.
    assert await enabled_tenant_ids("sample_digest") == []
    async with get_control_sessionmaker()() as session:
        assert await module_service.is_module_enabled(session, tenant.id, "sample_digest") is False

    # Données du module CONSERVÉES (décision D6).
    with tenant_context(ctx_for(tenant)):
        async with tenant_session() as session:
            digests = (await session.scalars(select(SampleDigestDigest))).all()
            assert len(digests) == 1
            assert digests[0].summary == "ancien"

    # Audit des deux transitions.
    with tenant_context(ctx_for(tenant)):
        async with tenant_session() as session:
            actions = [e.action for e in (await session.scalars(select(AuditEvent))).all()]
            assert "core.module.enabled" in actions
            assert "core.module.disabled" in actions
