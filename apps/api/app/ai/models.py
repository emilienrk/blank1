"""Politiques IA par tenant et événements d'usage — CONTROL-PLANE (plan global §6 :
« événements d'usage en control-plane »).

Trois tables :
- `tenant_ai_policies` : gouvernance par tenant (provider/modèle par défaut,
  providers autorisés, zéro-rétention, quota, fallback optionnel, BYOK **préparé**).
- `ai_usage_events` : un événement par appel IA (succès ou échec) — UNIQUEMENT des
  métriques (invariant n°3 : jamais de prompt ni de complétion). Fondation
  facturation (§2), d'où la conservation via agrégats.
- `ai_usage_daily` : agrégat (jour, tenant, provider, modèle) alimenté par le beat.

Les clés BYOK (`byok_keys_enc`) sont chiffrées `KeyProvider` (invariant n°6) —
champ préparé (décision D7), jamais exposé par l'API ni les logs.
"""

import enum
import uuid
from datetime import date, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import ControlPlaneBase


class AIProvider(enum.StrEnum):
    MISTRAL = "mistral"
    ANTHROPIC = "anthropic"
    OPENAI = "openai"


class UsageStatus(enum.StrEnum):
    OK = "ok"
    ERROR = "error"
    TIMEOUT = "timeout"


def _enum_values(enum_cls: type[enum.Enum]) -> list[str]:
    return [str(member.value) for member in enum_cls]


class TenantAIPolicy(ControlPlaneBase):
    __tablename__ = "tenant_ai_policies"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True
    )
    # Null → défaut plateforme (`ai_default_provider`/`ai_default_model`).
    default_provider: Mapped[str | None] = mapped_column(String(20), default=None)
    default_model: Mapped[str | None] = mapped_column(String(100), default=None)
    # Liste vide → tous les providers plateforme configurés sont autorisés.
    allowed_providers: Mapped[list[str]] = mapped_column(JSON(), default=list)
    zero_retention: Mapped[bool] = mapped_column(default=False)
    # Null → `ai_quota_default_monthly_tokens` (défaut plateforme, soft).
    monthly_token_quota: Mapped[int | None] = mapped_column(BigInteger(), default=None)
    # Prévu, non exposé (T5) : le quota reste soft tant que ce booléen est false.
    hard_limit_enabled: Mapped[bool] = mapped_column(default=False)
    # Fallback optionnel (décision D6) : activé par la présence d'un provider cible ;
    # désactivé par défaut. Incompatible avec zero_retention si la cible n'est pas ZDR.
    fallback_provider: Mapped[str | None] = mapped_column(String(20), default=None)
    fallback_model: Mapped[str | None] = mapped_column(String(100), default=None)
    # BYOK chiffré (KeyProvider) — PRÉPARÉ, jamais exposé (décision D7, invariant n°6).
    byok_keys_enc: Mapped[bytes | None] = mapped_column(LargeBinary(), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AIUsageEvent(ControlPlaneBase):
    __tablename__ = "ai_usage_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    # UUID d'un user global (control-plane) — null pour un appel système/module.
    user_id: Mapped[uuid.UUID | None] = mapped_column(default=None)
    # `core` (route de test) ou nom d'un module métier (Phase 7).
    module: Mapped[str] = mapped_column(String(50), default="core")
    provider: Mapped[str] = mapped_column(String(20))
    model: Mapped[str] = mapped_column(String(100))
    input_tokens: Mapped[int] = mapped_column(Integer(), default=0)
    output_tokens: Mapped[int] = mapped_column(Integer(), default=0)
    cached_tokens: Mapped[int] = mapped_column(Integer(), default=0)
    latency_ms: Mapped[int] = mapped_column(Integer(), default=0)
    estimated_cost_microeur: Mapped[int] = mapped_column(BigInteger(), default=0)
    price_version: Mapped[str] = mapped_column(String(20))
    status: Mapped[UsageStatus] = mapped_column(
        Enum(
            UsageStatus,
            name="ai_usage_status",
            native_enum=False,
            length=10,
            values_callable=_enum_values,
        )
    )
    # Nature technique de l'échec (jamais de contenu métier) — null si status=ok.
    error_kind: Mapped[str | None] = mapped_column(String(50), default=None)


class AIUsageDaily(ControlPlaneBase):
    __tablename__ = "ai_usage_daily"

    day: Mapped[date] = mapped_column(Date(), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True
    )
    provider: Mapped[str] = mapped_column(String(20), primary_key=True)
    model: Mapped[str] = mapped_column(String(100), primary_key=True)
    input_tokens: Mapped[int] = mapped_column(BigInteger(), default=0)
    output_tokens: Mapped[int] = mapped_column(BigInteger(), default=0)
    cached_tokens: Mapped[int] = mapped_column(BigInteger(), default=0)
    request_count: Mapped[int] = mapped_column(Integer(), default=0)
    error_count: Mapped[int] = mapped_column(Integer(), default=0)
    estimated_cost_microeur: Mapped[int] = mapped_column(BigInteger(), default=0)
