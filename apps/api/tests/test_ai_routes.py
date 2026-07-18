# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
"""Surfaces IA (Phase 6 T6) : route tenant `ai/chat` (permissions, politique ZDR)."""

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.ai.models import TenantAIPolicy
from app.core.config import Settings, get_settings
from app.core.db import get_control_sessionmaker
from app.main import create_app
from app.tenancy.provisioning import provision_tenant
from tests.ai_helpers import (
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
