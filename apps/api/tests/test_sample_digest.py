# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownLambdaType=false
"""Module d'exemple `sample_digest` bout en bout (Phase 7 T6).

Capabilities et gateway mockés (aucun provider ni réseau) : la tâche lit les mails
(mock), appelle l'IA (mock, metering `module=sample_digest` vérifié — la ventilation
par module du §6 devient réelle), écrit le digest en DB tenant, audite ; routes
lecture/run.
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.ai.models import AIUsageEvent
from app.audit.tenant_models import AuditEvent
from app.connectors.tenant_models import ConnectorProvider
from app.core.config import Settings, get_settings
from app.core.db import get_control_sessionmaker
from app.main import create_app
from app.modules.sample_digest import service as digest_service
from app.modules.sample_digest.tenant_models import SampleDigestDigest
from app.tenancy.context import tenant_context
from app.tenancy.provisioning import provision_tenant
from app.tenancy.session import tenant_session
from tests.ai_helpers import (
    fake_chat_response,
    install_fake_quota_valkey,
    reset_gateway_fns,
    reset_quota_valkey,
    set_chat_completion,
)
from tests.conftest import requires_postgres
from tests.connector_helpers import (
    create_connection,
    ctx_for,
    install_fake_valkey,
    reset_connector_throttle,
)
from tests.helpers import add_membership, create_session_token, create_user, reset_db_engines
from tests.module_helpers import FakeMail, enable_module_row

pytestmark = requires_postgres

HOST = {"host": "acme.app.example.fr"}
TASK = "sample_digest.daily_digest"


async def _member(tenant_id: uuid.UUID, email: str, role: str) -> str:
    user = await create_user(email)
    await add_membership(user.id, tenant_id, role)
    return await create_session_token(user.id)


async def test_task_generates_digest_meters_and_audits(
    db_env: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MISTRAL_API_KEY", "platform-key")
    get_settings.cache_clear()

    tenant = await provision_tenant("acme", "ACME")
    await create_connection(tenant, provider=ConnectorProvider.GOOGLE)
    await enable_module_row(tenant, "sample_digest")
    await reset_db_engines()

    # Capability mail mockée + réponse IA fixe + Valkey simulé (quota + verrou).
    monkeypatch.setattr(
        digest_service,
        "get_capability",
        lambda session, connection, capability: FakeMail(["Facture", "Réunion", "Congés"]),
    )
    set_chat_completion(fake_chat_response("• 3 sujets clés", prompt=30, completion=12))
    install_fake_quota_valkey()
    install_fake_valkey(monkeypatch)

    from app.automation import scheduler

    ran = await scheduler.run_periodic_unit("sample_digest", TASK, tenant.id)
    assert ran is True

    reset_gateway_fns()
    reset_quota_valkey()
    reset_connector_throttle()

    # Digest écrit en DB tenant.
    with tenant_context(ctx_for(tenant)):
        async with tenant_session() as session:
            digests = (await session.scalars(select(SampleDigestDigest))).all()
            assert len(digests) == 1
            assert digests[0].message_count == 3
            assert digests[0].summary == "• 3 sujets clés"
            actions = [e.action for e in (await session.scalars(select(AuditEvent))).all()]
            assert "sample_digest.digest_generated" in actions

    # Metering ventilé par module (control-plane).
    async with get_control_sessionmaker()() as session:
        events = (
            await session.scalars(
                select(AIUsageEvent).where(AIUsageEvent.module == "sample_digest")
            )
        ).all()
        assert len(events) == 1
        assert events[0].tenant_id == tenant.id


async def test_read_route_lists_digests(db_env: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    tenant = await provision_tenant("acme", "ACME")
    await enable_module_row(tenant, "sample_digest")
    member_token = await _member(tenant.id, "bob@example.com", "member")

    with tenant_context(ctx_for(tenant)):
        async with tenant_session() as session:
            session.add(SampleDigestDigest(summary="résumé du jour", message_count=2))
            await session.commit()
    await reset_db_engines()

    with TestClient(create_app()) as client:
        client.cookies.set(db_env.session_cookie_name, member_token)
        response = client.get("/api/v1/modules/sample_digest/digests", headers=HOST)
        assert response.status_code == 200
        body = response.json()
        assert len(body) == 1
        assert body[0]["summary"] == "résumé du jour"
        assert body[0]["message_count"] == 2
