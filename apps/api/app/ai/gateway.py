"""Le gateway IA : interface interne unique (Phase 6 T3, plan global §6).

Code appelé par le backend et les modules (Phase 7) — PAS une API publique.
Enchaînement par appel : contexte tenant obligatoire → politique → résolution
provider/modèle (validée contre `allowed_providers` et la liste ZDR) → clés
(plateforme ou BYOK) → appel LiteLLM sous timeout → fallback optionnel → metering
(succès comme échec, invariant n°4) et compteur de quota.

LiteLLM est un DÉTAIL D'IMPLÉMENTATION invisible des appelants (décision D2) :
les types aux frontières sont des Pydantic maison ; l'appel réel passe par des
frontières remplaçables (`set_completion_fn`/`set_embedding_fn`) — testées par
doublure, jamais de réseau en CI.
"""

import asyncio
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import structlog
from pydantic import BaseModel, Field

from app.ai import metering, quota
from app.ai.metering import UsageRecord
from app.ai.models import UsageStatus
from app.ai.policy import ResolvedPolicy, get_policy
from app.ai.pricing import PRICE_VERSION, TokenUsage, estimate_cost
from app.core.config import get_settings
from app.core.db import get_control_sessionmaker
from app.tenancy.context import TenantContext, current_tenant

logger = structlog.get_logger()


# --- Types aux frontières (Pydantic maison — LiteLLM jamais exposé, décision D2) ---


class Message(BaseModel):
    role: str
    content: str | None = None
    # Tool-calling (§6) — champs optionnels, sérialisés seulement si présents.
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[dict[str, Any]] | None = None

    def to_provider_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.name is not None:
            data["name"] = self.name
        if self.tool_call_id is not None:
            data["tool_call_id"] = self.tool_call_id
        if self.tool_calls is not None:
            data["tool_calls"] = self.tool_calls
        return data


class ToolDef(BaseModel):
    name: str
    description: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict)

    def to_provider_dict(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0


class ChatRequest(BaseModel):
    messages: list[Message]
    provider: str | None = None
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    tools: list[ToolDef] | None = None
    module: str = "core"
    user_id: uuid.UUID | None = None


class ChatResult(BaseModel):
    content: str
    provider: str
    model: str
    usage: Usage
    finish_reason: str | None = None
    tool_calls: list[dict[str, Any]] | None = None


class ChatStreamChunk(BaseModel):
    delta: str


class EmbedRequest(BaseModel):
    inputs: list[str]
    provider: str | None = None
    model: str | None = None
    module: str = "core"
    user_id: uuid.UUID | None = None


class EmbedResult(BaseModel):
    embeddings: list[list[float]]
    provider: str
    model: str
    usage: Usage


# --- Erreurs ---


class GatewayError(RuntimeError):
    """Erreur du gateway remontée aux appelants (le metering a déjà eu lieu)."""


class ProviderUnavailable(GatewayError):
    """Aucune clé (plateforme ni BYOK) configurée pour le provider demandé."""


# --- Frontières LiteLLM (remplaçables en test — aucun réseau en CI) ---


async def _default_completion_fn(**kwargs: Any) -> Any:
    # LiteLLM n'expose pas de types stables (décision D8) : isolé ici, jamais ailleurs.
    from litellm import acompletion  # pyright: ignore[reportUnknownVariableType]

    return await acompletion(**kwargs)


async def _default_embedding_fn(**kwargs: Any) -> Any:
    from litellm import aembedding  # pyright: ignore[reportUnknownVariableType]

    return await aembedding(**kwargs)


_completion_fn: Callable[..., Awaitable[Any]] = _default_completion_fn
_embedding_fn: Callable[..., Awaitable[Any]] = _default_embedding_fn


def set_completion_fn(fn: Callable[..., Awaitable[Any]] | None) -> None:
    global _completion_fn
    _completion_fn = fn if fn is not None else _default_completion_fn


def set_embedding_fn(fn: Callable[..., Awaitable[Any]] | None) -> None:
    global _embedding_fn
    _embedding_fn = fn if fn is not None else _default_embedding_fn


# --- Extraction défensive des réponses LiteLLM (typage lib incertain) ---


def _usage_from(raw: Any) -> TokenUsage:
    usage = getattr(raw, "usage", None)
    if usage is None:
        return TokenUsage(0, 0, 0)
    prompt = int(getattr(usage, "prompt_tokens", 0) or 0)
    completion = int(getattr(usage, "completion_tokens", 0) or 0)
    cached = 0
    details = getattr(usage, "prompt_tokens_details", None)
    if details is not None:
        cached = int(getattr(details, "cached_tokens", 0) or 0)
    return TokenUsage(input_tokens=prompt, output_tokens=completion, cached_tokens=cached)


def _first_choice(raw: Any) -> Any:
    choices = getattr(raw, "choices", None)
    if not choices:
        return None
    return choices[0]


class AIGateway:
    """Point d'entrée unique. Une instance suffit (`get_gateway()`)."""

    async def _load_policy(self, ctx: TenantContext) -> ResolvedPolicy:
        async with get_control_sessionmaker()() as session:
            return await get_policy(session, tenant_id=ctx.tenant_id, slug=ctx.slug)

    def _api_key(self, provider: str, policy: ResolvedPolicy) -> str:
        byok = policy.byok_key_for(provider)
        if byok:
            return byok
        settings = get_settings()
        platform = {
            "mistral": settings.mistral_api_key,
            "anthropic": settings.anthropic_api_key,
            "openai": settings.openai_api_key,
        }.get(provider, "")
        if not platform:
            msg = f"Provider {provider!r} indisponible : aucune clé configurée."
            raise ProviderUnavailable(msg)
        return platform

    async def chat(self, request: ChatRequest) -> ChatResult:
        ctx = current_tenant()
        policy = await self._load_policy(ctx)
        provider, model = policy.select(request.provider, request.model)
        try:
            return await self._chat_once(ctx, policy, provider, model, request)
        except GatewayError:
            if policy.fallback_provider is None:
                raise
            # Fallback optionnel (D6) : la cible est validée par la même `select`
            # (donc refusée si non-ZDR sous zéro-rétention). Metering du provider RÉEL.
            fb_provider, fb_model = policy.select(policy.fallback_provider, policy.fallback_model)
            logger.info("ai_fallback", tenant=ctx.slug, from_provider=provider, to=fb_provider)
            return await self._chat_once(ctx, policy, fb_provider, fb_model, request)

    async def _chat_once(
        self,
        ctx: TenantContext,
        policy: ResolvedPolicy,
        provider: str,
        model: str,
        request: ChatRequest,
    ) -> ChatResult:
        api_key = self._api_key(provider, policy)
        kwargs: dict[str, Any] = {
            "model": f"{provider}/{model}",
            "messages": [m.to_provider_dict() for m in request.messages],
            "api_key": api_key,
        }
        if request.temperature is not None:
            kwargs["temperature"] = request.temperature
        if request.max_tokens is not None:
            kwargs["max_tokens"] = request.max_tokens
        if request.tools is not None:
            kwargs["tools"] = [t.to_provider_dict() for t in request.tools]

        settings = get_settings()
        started = time.monotonic()
        try:
            raw = await asyncio.wait_for(
                _completion_fn(**kwargs), timeout=settings.ai_request_timeout_seconds
            )
        except TimeoutError as exc:
            await self._meter_wrap(
                ctx,
                policy,
                request.module,
                request.user_id,
                provider,
                model,
                UsageStatus.TIMEOUT,
                TokenUsage(0, 0, 0),
                started,
                "timeout",
            )
            msg = f"Appel {provider} interrompu après {settings.ai_request_timeout_seconds}s."
            raise GatewayError(msg) from exc
        except Exception as exc:  # toute erreur provider est metered puis remontée
            await self._meter_wrap(
                ctx,
                policy,
                request.module,
                request.user_id,
                provider,
                model,
                UsageStatus.ERROR,
                TokenUsage(0, 0, 0),
                started,
                type(exc).__name__,
            )
            raise GatewayError(f"Échec provider {provider}: {exc}") from exc

        usage = _usage_from(raw)
        latency_ms = int((time.monotonic() - started) * 1000)
        await self._meter_wrap(
            ctx,
            policy,
            request.module,
            request.user_id,
            provider,
            model,
            UsageStatus.OK,
            usage,
            started,
            None,
            latency_ms=latency_ms,
        )
        choice = _first_choice(raw)
        message = getattr(choice, "message", None)
        content = getattr(message, "content", None) or ""
        tool_calls_raw = getattr(message, "tool_calls", None)
        tool_calls = _normalize_tool_calls(tool_calls_raw)
        return ChatResult(
            content=content,
            provider=provider,
            model=model,
            usage=Usage(
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cached_tokens=usage.cached_tokens,
            ),
            finish_reason=getattr(choice, "finish_reason", None),
            tool_calls=tool_calls,
        )

    async def _meter_wrap(
        self,
        ctx: TenantContext,
        policy: ResolvedPolicy,
        module: str,
        user_id: uuid.UUID | None,
        provider: str,
        model: str,
        status: UsageStatus,
        usage: TokenUsage,
        started: float,
        error_kind: str | None,
        *,
        latency_ms: int | None = None,
    ) -> None:
        cost = estimate_cost(provider, model, usage)
        elapsed = latency_ms if latency_ms is not None else int((time.monotonic() - started) * 1000)
        await metering.record_usage(
            UsageRecord(
                tenant_id=ctx.tenant_id,
                user_id=user_id,
                module=module,
                provider=provider,
                model=model,
                status=status,
                price_version=PRICE_VERSION,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cached_tokens=usage.cached_tokens,
                latency_ms=elapsed,
                estimated_cost_microeur=cost,
                error_kind=error_kind,
            )
        )
        total_tokens = usage.input_tokens + usage.output_tokens
        if total_tokens > 0:
            await quota.record_and_alert(
                ctx, added_tokens=total_tokens, quota=policy.monthly_token_quota
            )

    async def chat_stream(self, request: ChatRequest) -> AsyncIterator[ChatStreamChunk]:
        ctx = current_tenant()
        policy = await self._load_policy(ctx)
        provider, model = policy.select(request.provider, request.model)
        api_key = self._api_key(provider, policy)
        kwargs: dict[str, Any] = {
            "model": f"{provider}/{model}",
            "messages": [m.to_provider_dict() for m in request.messages],
            "api_key": api_key,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if request.temperature is not None:
            kwargs["temperature"] = request.temperature
        if request.max_tokens is not None:
            kwargs["max_tokens"] = request.max_tokens

        started = time.monotonic()
        status = UsageStatus.OK
        error_kind: str | None = None
        usage = TokenUsage(0, 0, 0)
        try:
            stream = await _completion_fn(**kwargs)
            async for chunk in stream:
                choice = _first_choice(chunk)
                delta = getattr(getattr(choice, "delta", None), "content", None)
                if delta:
                    yield ChatStreamChunk(delta=str(delta))
                chunk_usage = _usage_from(chunk)
                if chunk_usage.input_tokens or chunk_usage.output_tokens:
                    usage = chunk_usage
        except GeneratorExit:
            # Consommateur interrompu : événement d'erreur avec les tokens connus (T4).
            status = UsageStatus.ERROR
            error_kind = "stream_interrupted"
            raise
        except Exception as exc:
            status = UsageStatus.ERROR
            error_kind = type(exc).__name__
            raise GatewayError(f"Échec streaming {provider}: {exc}") from exc
        finally:
            await self._meter_wrap(
                ctx,
                policy,
                request.module,
                request.user_id,
                provider,
                model,
                status,
                usage,
                started,
                error_kind,
            )

    async def embed(self, request: EmbedRequest) -> EmbedResult:
        ctx = current_tenant()
        policy = await self._load_policy(ctx)
        provider, model = policy.select(request.provider, request.model)
        api_key = self._api_key(provider, policy)
        settings = get_settings()
        started = time.monotonic()
        try:
            raw = await asyncio.wait_for(
                _embedding_fn(model=f"{provider}/{model}", input=request.inputs, api_key=api_key),
                timeout=settings.ai_request_timeout_seconds,
            )
        except TimeoutError as exc:
            await self._meter_wrap(
                ctx,
                policy,
                request.module,
                request.user_id,
                provider,
                model,
                UsageStatus.TIMEOUT,
                TokenUsage(0, 0, 0),
                started,
                "timeout",
            )
            raise GatewayError(f"Embeddings {provider} interrompus (timeout).") from exc
        except Exception as exc:
            await self._meter_wrap(
                ctx,
                policy,
                request.module,
                request.user_id,
                provider,
                model,
                UsageStatus.ERROR,
                TokenUsage(0, 0, 0),
                started,
                type(exc).__name__,
            )
            raise GatewayError(f"Échec embeddings {provider}: {exc}") from exc

        usage = _usage_from(raw)
        await self._meter_wrap(
            ctx,
            policy,
            request.module,
            request.user_id,
            provider,
            model,
            UsageStatus.OK,
            usage,
            started,
            None,
        )
        vectors = [list(map(float, item["embedding"])) for item in getattr(raw, "data", [])]
        return EmbedResult(
            provider=provider,
            model=model,
            embeddings=vectors,
            usage=Usage(
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cached_tokens=usage.cached_tokens,
            ),
        )


def _normalize_tool_calls(raw: Any) -> list[dict[str, Any]] | None:
    if not raw:
        return None
    calls: list[dict[str, Any]] = []
    for call in raw:
        function = getattr(call, "function", None)
        calls.append(
            {
                "id": getattr(call, "id", None),
                "name": getattr(function, "name", None),
                "arguments": getattr(function, "arguments", None),
            }
        )
    return calls


_gateway: AIGateway | None = None


def get_gateway() -> AIGateway:
    global _gateway
    if _gateway is None:
        _gateway = AIGateway()
    return _gateway
