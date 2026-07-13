"""Route tenant du gateway IA (Phase 6 T6) — surface MINIMALE.

Une seule route : `POST /api/v1/ai/chat` (`core.ai.use`, owner/admin par défaut).
C'est l'écho du gateway pour la démo et le smoke test staging — PAS une UI de chat.
Le socle fournit l'infrastructure ; les usages réels viennent des modules (Phase 7),
qui appellent `AIGateway` directement, sans passer par HTTP.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.ai.gateway import ChatRequest, GatewayError, Message, ProviderUnavailable, get_gateway
from app.ai.policy import PolicyError
from app.auth.deps import CurrentAuth, current_auth
from app.auth.permissions import require_permission
from app.tenancy.context import TenantContext

router = APIRouter(prefix="/ai", tags=["ai"])


class ChatMessageIn(BaseModel):
    role: str
    content: str | None = None


class ChatIn(BaseModel):
    messages: list[ChatMessageIn]
    # Demande explicite validée contre la politique (allowed_providers + ZDR) —
    # null → défauts de la politique du tenant.
    provider: str | None = None
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None


class UsageOut(BaseModel):
    input_tokens: int
    output_tokens: int
    cached_tokens: int


class ChatOut(BaseModel):
    content: str
    provider: str
    model: str
    usage: UsageOut
    finish_reason: str | None = None
    tool_calls: list[dict[str, Any]] | None = None


@router.post("/chat", operation_id="aiChat")
async def ai_chat(
    payload: ChatIn,
    ctx: Annotated[TenantContext, Depends(require_permission("core.ai.use"))],
    auth: Annotated[CurrentAuth, Depends(current_auth)],
) -> ChatOut:
    request = ChatRequest(
        messages=[Message(role=m.role, content=m.content) for m in payload.messages],
        provider=payload.provider,
        model=payload.model,
        temperature=payload.temperature,
        max_tokens=payload.max_tokens,
        module="core",
        user_id=auth.user.id,
    )
    try:
        result = await get_gateway().chat(request)
    except PolicyError as exc:
        # Refus de politique (provider non autorisé, hors liste ZDR) : 403 explicite.
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ProviderUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except GatewayError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return ChatOut(
        content=result.content,
        provider=result.provider,
        model=result.model,
        usage=UsageOut(
            input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens,
            cached_tokens=result.usage.cached_tokens,
        ),
        finish_reason=result.finish_reason,
        tool_calls=result.tool_calls,
    )
