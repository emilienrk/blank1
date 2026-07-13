"""Helpers partagés des tests du gateway IA (Phase 6).

Doublures LiteLLM (aucun réseau en CI, décision D8) : réponses chat/stream/embed
factices injectées par `set_completion_fn`/`set_embedding_fn`, et fakeredis pour
le compteur de quota. Aucun test ne consomme de clé réelle.
"""

# Objets factices au typage volontairement souple (imitent les réponses LiteLLM).
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false

import types
import uuid
from collections.abc import AsyncIterator

from app.ai import gateway, quota
from app.tenancy.context import TenantContext
from app.tenancy.models import Tenant, TenantState


def fake_usage(prompt: int, completion: int, cached: int = 0) -> object:
    details = types.SimpleNamespace(cached_tokens=cached) if cached else None
    return types.SimpleNamespace(
        prompt_tokens=prompt,
        completion_tokens=completion,
        prompt_tokens_details=details,
    )


def fake_chat_response(
    content: str, *, prompt: int = 10, completion: int = 5, cached: int = 0
) -> object:
    message = types.SimpleNamespace(content=content, tool_calls=None)
    choice = types.SimpleNamespace(message=message, delta=None, finish_reason="stop")
    return types.SimpleNamespace(choices=[choice], usage=fake_usage(prompt, completion, cached))


def fake_embed_response(vectors: list[list[float]], *, prompt: int = 3) -> object:
    data = [{"embedding": vec} for vec in vectors]
    return types.SimpleNamespace(data=data, usage=fake_usage(prompt, 0))


def set_chat_completion(response: object) -> None:
    """Installe une réponse chat non-streaming fixe."""

    async def _fn(**_kwargs: object) -> object:
        return response

    gateway.set_completion_fn(_fn)


def set_chat_stream(chunks: list[str], *, prompt: int = 4, completion: int = 3) -> None:
    """Installe un flux : un chunk par fragment, usage sur le dernier."""

    async def _fn(**_kwargs: object) -> AsyncIterator[object]:
        async def _gen() -> AsyncIterator[object]:
            last = len(chunks) - 1
            for i, fragment in enumerate(chunks):
                delta = types.SimpleNamespace(content=fragment)
                usage = fake_usage(prompt, completion) if i == last else None
                yield types.SimpleNamespace(
                    choices=[types.SimpleNamespace(delta=delta)], usage=usage
                )

        return _gen()

    gateway.set_completion_fn(_fn)


def reset_gateway_fns() -> None:
    gateway.set_completion_fn(None)
    gateway.set_embedding_fn(None)


def install_fake_quota_valkey() -> None:
    import fakeredis.aioredis

    quota.set_valkey_client(fakeredis.aioredis.FakeRedis())


def reset_quota_valkey() -> None:
    quota.set_valkey_client(None)


def ctx_for(tenant: Tenant) -> TenantContext:
    return TenantContext(
        tenant_id=tenant.id,
        slug=tenant.slug,
        state=tenant.state,
        db_name=tenant.db_name,
        db_host=tenant.db_host,
        role=None,
    )


def ctx_stub(tenant_id: uuid.UUID | None = None, slug: str = "acme") -> TenantContext:
    """Contexte minimal pour les tests de quota purs (sans DB tenant réelle)."""
    return TenantContext(
        tenant_id=tenant_id or uuid.uuid4(),
        slug=slug,
        state=TenantState.ACTIVE,
        db_name=f"{slug}_db",
        db_host="default",
        role=None,
    )
