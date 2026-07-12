"""Politiques IA (Phase 6 T3) : résolution + enforcement zéro-rétention et fallback.

Tests purs (aucune DB) : la logique de `select`/`_resolve` opère sur des objets
en mémoire. La liste ZDR est en code (décision D5) : une violation est un REFUS
explicite, jamais une dégradation silencieuse.
"""

import json
import uuid

import pytest

from app.ai.models import TenantAIPolicy
from app.ai.policy import PolicyError, ResolvedPolicy, resolve_record
from app.core.crypto import get_key_provider


def _policy(**overrides: object) -> ResolvedPolicy:
    base: dict[str, object] = {
        "slug": "acme",
        "default_provider": "mistral",
        "default_model": "mistral-small-latest",
        "allowed_providers": frozenset(),
        "zero_retention": False,
        "monthly_token_quota": 1_000_000,
        "hard_limit_enabled": False,
        "fallback_provider": None,
        "fallback_model": None,
        "byok_keys": {},
    }
    base.update(overrides)
    return ResolvedPolicy(**base)  # type: ignore[arg-type]


def test_defaults_applied_when_nothing_requested() -> None:
    provider, model = _policy().select(None, None)
    assert provider == "mistral"
    assert model == "mistral-small-latest"


def test_provider_outside_allowed_is_refused() -> None:
    policy = _policy(allowed_providers=frozenset({"mistral"}))
    with pytest.raises(PolicyError):
        policy.select("anthropic", "claude-3-5-sonnet-latest")


def test_zero_retention_refuses_non_zdr_even_explicitly() -> None:
    policy = _policy(zero_retention=True)
    # Demande explicite hors liste ZDR → refus (invariant n°5), pas de dégradation.
    with pytest.raises(PolicyError):
        policy.select("openai", "gpt-4o")
    # Mistral (ZDR) reste autorisé.
    assert policy.select("mistral", "mistral-small-latest") == ("mistral", "mistral-small-latest")


def test_fallback_disabled_by_default() -> None:
    assert _policy().fallback_provider is None


def test_fallback_target_validated_by_same_select_under_zero_retention() -> None:
    # Fallback non-ZDR sous zéro-rétention → refusé par la même `select` (D5/D6).
    policy = _policy(zero_retention=True, fallback_provider="openai", fallback_model="gpt-4o")
    with pytest.raises(PolicyError):
        policy.select(policy.fallback_provider, policy.fallback_model)


def test_resolve_uses_platform_defaults_when_no_record() -> None:
    resolved = resolve_record("acme", None)
    assert resolved.default_provider  # défaut plateforme (config)
    assert resolved.monthly_token_quota > 0
    assert resolved.zero_retention is False
    assert resolved.byok_keys == {}


def test_resolve_decrypts_byok_keys() -> None:
    # Plomberie BYOK (décision D7) : la clé chiffrée est déchiffrée à la résolution.
    sealed = get_key_provider().encrypt(json.dumps({"mistral": "sk-tenant"}).encode())
    record = TenantAIPolicy(
        tenant_id=uuid.uuid4(),
        default_provider="mistral",
        default_model="mistral-small-latest",
        allowed_providers=[],
        zero_retention=False,
        byok_keys_enc=sealed,
    )
    resolved = resolve_record("acme", record)
    assert resolved.byok_key_for("mistral") == "sk-tenant"
