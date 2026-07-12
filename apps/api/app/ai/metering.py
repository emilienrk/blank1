"""Metering — un événement d'usage par appel IA (Phase 6 T4, invariant n°4).

Insertion directe **best-effort** (décision D3) : un échec d'insertion logge en
erreur mais ne fait JAMAIS échouer la réponse IA. UNIQUEMENT des métriques —
jamais de prompt ni de complétion (invariant n°3). L'événement est écrit même en
erreur/timeout (tokens connus, statut renseigné).
"""

import uuid
from dataclasses import dataclass

import structlog

from app.ai.models import AIUsageEvent, UsageStatus
from app.core.db import get_control_sessionmaker

logger = structlog.get_logger()


@dataclass(frozen=True, slots=True)
class UsageRecord:
    tenant_id: uuid.UUID
    module: str
    provider: str
    model: str
    status: UsageStatus
    price_version: str
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    latency_ms: int = 0
    estimated_cost_microeur: int = 0
    user_id: uuid.UUID | None = None
    error_kind: str | None = None


async def record_usage(record: UsageRecord) -> None:
    """Insère l'événement en control-plane (best-effort) : l'échec est loggé, pas levé."""
    try:
        async with get_control_sessionmaker()() as session:
            session.add(
                AIUsageEvent(
                    tenant_id=record.tenant_id,
                    user_id=record.user_id,
                    module=record.module,
                    provider=record.provider,
                    model=record.model,
                    input_tokens=record.input_tokens,
                    output_tokens=record.output_tokens,
                    cached_tokens=record.cached_tokens,
                    latency_ms=record.latency_ms,
                    estimated_cost_microeur=record.estimated_cost_microeur,
                    price_version=record.price_version,
                    status=record.status,
                    error_kind=record.error_kind,
                )
            )
            await session.commit()
    except Exception:  # le metering ne doit jamais casser une réponse IA
        logger.exception(
            "ai_metering_insert_failed",
            tenant_id=str(record.tenant_id),
            provider=record.provider,
            model=record.model,
            status=record.status.value,
        )
