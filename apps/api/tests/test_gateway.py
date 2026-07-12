# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
"""Le gateway IA (Phase 6 T3/T4) : résolution, appel, metering — succès comme échec.

LiteLLM est doublé (aucun réseau). Postgres réel pour le control-plane (metering)
et la DB tenant (audit d'alerte). Le compteur de quota est sur fakeredis.
"""

import uuid
from collections.abc import AsyncGenerator, Iterator
from typing import cast

import pytest
from sqlalchemy import select

from app.ai.gateway import ChatRequest, ChatStreamChunk, Message, get_gateway
from app.ai.models import AIUsageEvent, TenantAIPolicy, UsageStatus
from app.ai.policy import PolicyError
from app.core.config import Settings, get_settings
from app.core.db import get_control_sessionmaker
from app.tenancy.context import TenantContextError, tenant_context
from app.tenancy.provisioning import provision_tenant
from tests.ai_helpers import (
    ctx_for,
    fake_chat_response,
    install_fake_quota_valkey,
    reset_gateway_fns,
    reset_quota_valkey,
    set_chat_completion,
    set_chat_stream,
)
from tests.conftest import requires_postgres
from tests.helpers import reset_db_engines


@pytest.fixture(autouse=True)
def ai_doubles() -> Iterator[None]:
    install_fake_quota_valkey()
    yield
    reset_gateway_fns()
    reset_quota_valkey()


async def _events() -> list[AIUsageEvent]:
    async with get_control_sessionmaker()() as session:
        return list((await session.scalars(select(AIUsageEvent))).all())


async def _add_policy(tenant_id: uuid.UUID, **fields: object) -> None:
    async with get_control_sessionmaker()() as session:
        session.add(TenantAIPolicy(tenant_id=tenant_id, **fields))
        await session.commit()


def _with_mistral_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MISTRAL_API_KEY", "sk-platform")
    get_settings.cache_clear()


async def test_chat_without_tenant_context_is_refused() -> None:
    # Extension de l'invariant racine n°1 : aucun appel IA sans tenant courant.
    with pytest.raises(TenantContextError):
        await get_gateway().chat(ChatRequest(messages=[Message(role="user", content="hi")]))


@requires_postgres
async def test_default_policy_applied_and_metered(
    db_env: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = await provision_tenant("acme", "ACME")
    _with_mistral_key(monkeypatch)
    await reset_db_engines()
    set_chat_completion(fake_chat_response("bonjour", prompt=12, completion=8))

    with tenant_context(ctx_for(tenant)):
        result = await get_gateway().chat(
            ChatRequest(messages=[Message(role="user", content="salut")])
        )

    assert result.content == "bonjour"
    assert result.provider == "mistral"  # défaut plateforme de la politique
    assert result.usage.input_tokens == 12
    assert result.usage.output_tokens == 8

    await reset_db_engines()
    events = await _events()
    assert len(events) == 1
    event = events[0]
    assert event.status is UsageStatus.OK
    assert event.provider == "mistral"
    assert event.input_tokens == 12
    assert event.price_version
    assert event.estimated_cost_microeur > 0


@requires_postgres
async def test_explicit_provider_outside_allowed_is_refused(
    db_env: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = await provision_tenant("acme", "ACME")
    await _add_policy(tenant.id, allowed_providers=["mistral"])
    _with_mistral_key(monkeypatch)
    await reset_db_engines()
    set_chat_completion(fake_chat_response("nope"))

    with tenant_context(ctx_for(tenant)), pytest.raises(PolicyError):
        await get_gateway().chat(
            ChatRequest(
                messages=[Message(role="user", content="x")],
                provider="openai",
                model="gpt-4o",
            )
        )

    # Refus de politique = pas d'appel provider → aucun événement.
    await reset_db_engines()
    assert await _events() == []


@requires_postgres
async def test_timeout_is_metered(db_env: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.ai import gateway

    tenant = await provision_tenant("acme", "ACME")
    _with_mistral_key(monkeypatch)
    await reset_db_engines()

    async def _timeout_fn(**_kwargs: object) -> object:
        raise TimeoutError

    gateway.set_completion_fn(_timeout_fn)

    with tenant_context(ctx_for(tenant)), pytest.raises(gateway.GatewayError):
        await get_gateway().chat(ChatRequest(messages=[Message(role="user", content="x")]))

    await reset_db_engines()
    events = await _events()
    assert len(events) == 1
    assert events[0].status is UsageStatus.TIMEOUT
    assert events[0].error_kind == "timeout"


@requires_postgres
async def test_streaming_yields_chunks_then_final_event(
    db_env: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = await provision_tenant("acme", "ACME")
    _with_mistral_key(monkeypatch)
    await reset_db_engines()
    set_chat_stream(["Bon", "jour"], prompt=5, completion=4)

    collected: list[str] = []
    with tenant_context(ctx_for(tenant)):
        async for chunk in get_gateway().chat_stream(
            ChatRequest(messages=[Message(role="user", content="salut")])
        ):
            collected.append(chunk.delta)

    assert "".join(collected) == "Bonjour"
    await reset_db_engines()
    events = await _events()
    assert len(events) == 1
    assert events[0].status is UsageStatus.OK
    assert events[0].output_tokens == 4


@requires_postgres
async def test_interrupted_stream_metered_as_error(
    db_env: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = await provision_tenant("acme", "ACME")
    _with_mistral_key(monkeypatch)
    await reset_db_engines()
    set_chat_stream(["a", "b", "c"])

    with tenant_context(ctx_for(tenant)):
        agen = cast(
            "AsyncGenerator[ChatStreamChunk, None]",
            get_gateway().chat_stream(ChatRequest(messages=[Message(role="user", content="x")])),
        )
        await agen.__anext__()  # un seul chunk consommé
        await agen.aclose()  # interruption → GeneratorExit → événement d'erreur

    await reset_db_engines()
    events = await _events()
    assert len(events) == 1
    assert events[0].status is UsageStatus.ERROR
    assert events[0].error_kind == "stream_interrupted"


@requires_postgres
async def test_byok_key_used_when_present(
    db_env: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    from app.ai import gateway
    from app.core.crypto import get_key_provider

    tenant = await provision_tenant("acme", "ACME")
    sealed = get_key_provider().encrypt(json.dumps({"mistral": "sk-tenant-byok"}).encode())
    await _add_policy(tenant.id, byok_keys_enc=sealed)
    # Pas de clé plateforme : seule la clé BYOK doit permettre l'appel (plomberie D7).
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    get_settings.cache_clear()
    await reset_db_engines()

    seen: dict[str, object] = {}

    async def _capture_fn(**kwargs: object) -> object:
        seen.update(kwargs)
        return fake_chat_response("ok")

    gateway.set_completion_fn(_capture_fn)

    with tenant_context(ctx_for(tenant)):
        await get_gateway().chat(ChatRequest(messages=[Message(role="user", content="x")]))

    assert seen["api_key"] == "sk-tenant-byok"
