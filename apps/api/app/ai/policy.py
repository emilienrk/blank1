"""Politiques IA par tenant : lecture/écriture + enforcement (Phase 6 T3).

`get_policy` résout la politique effective (défauts plateforme si absente). La
sélection provider/modèle valide la demande du code appelant contre
`allowed_providers` ET la politique zéro-rétention (décision D5) : sous
`zero_retention`, seuls les providers/modèles de la LISTE ZDR EN CODE sont
acceptés — un appel explicite hors liste est **refusé** (`PolicyError`), jamais
dégradé silencieusement (invariant n°5 de la phase).
"""

import json
import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.models import TenantAIPolicy
from app.core.config import get_settings
from app.core.crypto import get_key_provider

# Liste FERMÉE des providers à rétention nulle contractuelle (décision D5).
# Mistral (France) d'abord ; les endpoints ZDR d'autres providers s'ajoutent
# ici par PR après vérification contractuelle — jamais par configuration libre.
ZERO_RETENTION_PROVIDERS: frozenset[str] = frozenset({"mistral"})


class PolicyError(RuntimeError):
    """Refus explicite : provider non autorisé, ou hors liste ZDR sous zéro-rétention."""


@dataclass(frozen=True, slots=True)
class ResolvedPolicy:
    slug: str
    default_provider: str
    default_model: str
    # Vide → tous les providers plateforme configurés sont autorisés.
    allowed_providers: frozenset[str]
    zero_retention: bool
    monthly_token_quota: int
    hard_limit_enabled: bool
    fallback_provider: str | None
    fallback_model: str | None
    # Clés BYOK déchiffrées (provider → clé) — vide si aucune (décision D7).
    byok_keys: dict[str, str]

    def select(self, provider: str | None, model: str | None) -> tuple[str, str]:
        """Résout (provider, modèle) et valide contre `allowed_providers` et la
        politique zéro-rétention. Lève `PolicyError` sur refus (jamais de dégradation)."""
        resolved_provider = provider or self.default_provider
        resolved_model = model or self.default_model
        if self.allowed_providers and resolved_provider not in self.allowed_providers:
            msg = f"Provider {resolved_provider!r} non autorisé par la politique du tenant."
            raise PolicyError(msg)
        if self.zero_retention and resolved_provider not in ZERO_RETENTION_PROVIDERS:
            msg = (
                f"Provider {resolved_provider!r} refusé : le tenant est en zéro-rétention, "
                f"seuls les providers ZDR ({', '.join(sorted(ZERO_RETENTION_PROVIDERS))}) "
                "sont autorisés."
            )
            raise PolicyError(msg)
        return resolved_provider, resolved_model

    def byok_key_for(self, provider: str) -> str | None:
        return self.byok_keys.get(provider)


def _decrypt_byok(sealed: bytes | None) -> dict[str, str]:
    if not sealed:
        return {}
    raw = get_key_provider().decrypt(sealed).decode()
    data = json.loads(raw)
    return {str(k): str(v) for k, v in data.items()}


def resolve_record(slug: str, record: TenantAIPolicy | None) -> ResolvedPolicy:
    settings = get_settings()
    if record is None:
        return ResolvedPolicy(
            slug=slug,
            default_provider=settings.ai_default_provider,
            default_model=settings.ai_default_model,
            allowed_providers=frozenset(),
            zero_retention=False,
            monthly_token_quota=settings.ai_quota_default_monthly_tokens,
            hard_limit_enabled=False,
            fallback_provider=None,
            fallback_model=None,
            byok_keys={},
        )
    return ResolvedPolicy(
        slug=slug,
        default_provider=record.default_provider or settings.ai_default_provider,
        default_model=record.default_model or settings.ai_default_model,
        allowed_providers=frozenset(record.allowed_providers),
        zero_retention=record.zero_retention,
        monthly_token_quota=(
            record.monthly_token_quota
            if record.monthly_token_quota is not None
            else settings.ai_quota_default_monthly_tokens
        ),
        hard_limit_enabled=record.hard_limit_enabled,
        fallback_provider=record.fallback_provider,
        fallback_model=record.fallback_model,
        byok_keys=_decrypt_byok(record.byok_keys_enc),
    )


async def get_policy(session: AsyncSession, *, tenant_id: uuid.UUID, slug: str) -> ResolvedPolicy:
    """Politique effective du tenant (défauts plateforme si aucune ligne)."""
    record = await session.get(TenantAIPolicy, tenant_id)
    return resolve_record(slug, record)
