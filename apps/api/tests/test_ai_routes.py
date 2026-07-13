# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
"""Surfaces IA (Phase 6 T6) : route tenant `ai/chat` (permissions) + routes admin
(usage, politique auditée).
"""

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.ai.models import AIUsageDaily, TenantAIPolicy
from app.audit.tenant_models import AuditEvent
from app.core.config import Settings, get_settings
from app.core.db import get_control_sessionmaker
from app.directory.models import User
from app.main import create_app
from app.tenancy.context import tenant_context
from app.tenancy.engine_manager import get_engine_manager
from app.tenancy.provisioning import provision_tenant
from tests.ai_helpers import (
    ctx_for,
    fake_chat_response,
    install_fake_quota_valkey,
    reset_gateway_fns,
    reset_quota_valkey,
    set_chat_completion,
)
from tests.conftest import requires_postgres
from tests.helpers import add_membership, create_session_token, create_user, reset_db_engines

pytestmark = requires_postgres

HOST = {"host": "acme.app.example.fr"}


@pytest.fixture(autouse=True)
def ai_doubles() -> Iterator[None]:
    install_fake_quota_valkey()
    yield
    reset_gateway_fns()
    reset_quota_valkey()


async def _member(tenant_id: uuid.UUID, email: str, role: str) -> str:
    user = await create_user(email)
    await add_membership(user.id, tenant_id, role)
    return await create_session_token(user.id)


async def _promote(email: str) -> str:
    user = await create_user(email)
    async with get_control_sessionmaker()() as session:
        stored = await session.get(User, user.id)
        assert stored is not None
        stored.is_platform_admin = True
        await session.commit()
    return await create_session_token(user.id)


# --- Route tenant ai/chat : matrice de permissions ---


async def test_chat_permission_matrix(db_env: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    tenant = await provision_tenant("acme", "ACME")
    member_token = await _member(tenant.id, "bob@example.com", "member")
    admin_token = await _member(tenant.id, "alice@example.com", "admin")
    monkeypatch.setenv("MISTRAL_API_KEY", "sk-platform")
    get_settings.cache_clear()
    await reset_db_engines()
    install_fake_quota_valkey()
    set_chat_completion(fake_chat_response("bonjour", prompt=7, completion=3))
    body = {"messages": [{"role": "user", "content": "salut"}]}

    with TestClient(create_app()) as client:
        # Anonyme → 401.
        assert client.post("/api/v1/ai/chat", json=body, headers=HOST).status_code == 401

        # core.ai.use réservé owner/admin : un membre est refusé.
        client.cookies.set(db_env.session_cookie_name, member_token)
        assert client.post("/api/v1/ai/chat", json=body, headers=HOST).status_code == 403

        client.cookies.set(db_env.session_cookie_name, admin_token)
        response = client.post("/api/v1/ai/chat", json=body, headers=HOST)
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["content"] == "bonjour"
        assert payload["provider"] == "mistral"
        assert payload["usage"]["input_tokens"] == 7


async def test_chat_zero_retention_refuses_non_zdr_provider(
    db_env: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = await provision_tenant("acme", "ACME")
    admin_token = await _member(tenant.id, "alice@example.com", "admin")
    async with get_control_sessionmaker()() as session:
        session.add(TenantAIPolicy(tenant_id=tenant.id, zero_retention=True))
        await session.commit()
    monkeypatch.setenv("MISTRAL_API_KEY", "sk-platform")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    get_settings.cache_clear()
    await reset_db_engines()
    install_fake_quota_valkey()
    set_chat_completion(fake_chat_response("nope"))

    with TestClient(create_app()) as client:
        client.cookies.set(db_env.session_cookie_name, admin_token)
        # Demande explicite d'un provider non-ZDR sous zéro-rétention → 403 clair.
        response = client.post(
            "/api/v1/ai/chat",
            json={
                "messages": [{"role": "user", "content": "x"}],
                "provider": "openai",
                "model": "gpt-4o",
            },
            headers=HOST,
        )
        assert response.status_code == 403


# --- Routes admin : usage + politique ---


async def test_admin_usage_requires_platform_admin_and_shows_overrun(db_env: Settings) -> None:
    tenant = await provision_tenant("acme", "ACME")
    from datetime import UTC, datetime

    async with get_control_sessionmaker()() as session:
        # Quota bas + usage au-dessus → dépassement visible au back-office.
        session.add(TenantAIPolicy(tenant_id=tenant.id, monthly_token_quota=1_000))
        session.add(
            AIUsageDaily(
                day=datetime.now(UTC).date(),
                tenant_id=tenant.id,
                provider="mistral",
                model="mistral-small-latest",
                input_tokens=4_000,
                output_tokens=1_000,
                request_count=10,
                error_count=1,
                estimated_cost_microeur=1234,
            )
        )
        await session.commit()

    regular_token = await _member(tenant.id, "bob@example.com", "member")
    admin_token = await _promote("root@example.com")
    await reset_db_engines()

    with TestClient(create_app()) as client:
        assert client.get("/api/v1/admin/ai/usage").status_code == 401
        client.cookies.set(db_env.session_cookie_name, regular_token)
        assert client.get("/api/v1/admin/ai/usage").status_code == 403

        client.cookies.set(db_env.session_cookie_name, admin_token)
        response = client.get("/api/v1/admin/ai/usage")
        assert response.status_code == 200
        rows = {row["slug"]: row for row in response.json()}
        assert rows["acme"]["total_tokens"] == 5_000
        assert rows["acme"]["over_quota"] is True


async def test_admin_set_policy_is_audited(db_env: Settings) -> None:
    tenant = await provision_tenant("acme", "ACME")
    admin_token = await _promote("root@example.com")
    await reset_db_engines()

    with TestClient(create_app()) as client:
        client.cookies.set(db_env.session_cookie_name, admin_token)
        response = client.put(
            "/api/v1/admin/tenants/acme/ai-policy",
            json={
                "default_provider": "mistral",
                "default_model": "mistral-large-latest",
                "allowed_providers": ["mistral"],
                "zero_retention": True,
                "monthly_token_quota": 2_000_000,
                "hard_limit_enabled": False,
                "fallback_provider": None,
                "fallback_model": None,
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["zero_retention"] is True

    await reset_db_engines()
    async with get_control_sessionmaker()() as session:
        record = await session.get(TenantAIPolicy, tenant.id)
        assert record is not None
        assert record.default_model == "mistral-large-latest"
        assert record.zero_retention is True

    # Changement de politique audité (T6) en DB tenant.
    ctx = ctx_for(tenant)
    with tenant_context(ctx):
        async with get_engine_manager().session(ctx) as session:
            actions = [e.action for e in (await session.scalars(select(AuditEvent))).all()]
    assert "core.ai.policy_changed" in actions


async def test_admin_set_policy_rejects_unknown_provider(db_env: Settings) -> None:
    await provision_tenant("acme", "ACME")
    admin_token = await _promote("root@example.com")
    await reset_db_engines()

    with TestClient(create_app()) as client:
        client.cookies.set(db_env.session_cookie_name, admin_token)
        response = client.put(
            "/api/v1/admin/tenants/acme/ai-policy",
            json={
                "default_provider": "not-a-provider",
                "default_model": "x",
                "allowed_providers": [],
                "zero_retention": False,
                "monthly_token_quota": None,
                "hard_limit_enabled": False,
                "fallback_provider": None,
                "fallback_model": None,
            },
        )
        assert response.status_code == 400
