"""Table de prix versionnée (Phase 6 T2, décision D4).

Coûts calculés sur des cas connus ; modèle inconnu → 0 + warning, jamais bloquant.
"""

import pytest

from app.ai import pricing
from app.ai.pricing import TokenUsage, estimate_cost


def test_input_and_output_priced_per_million() -> None:
    # mistral-small : 200_000 µ€/Mtok in, 600_000 µ€/Mtok out.
    assert estimate_cost("mistral", "mistral-small-latest", TokenUsage(1_000_000, 0, 0)) == 200_000
    assert estimate_cost("mistral", "mistral-small-latest", TokenUsage(0, 1_000_000, 0)) == 600_000


def test_cached_tokens_billed_at_reduced_rate() -> None:
    # Anthropic haiku : cached (80_000) bien moins cher qu'un input plein (800_000).
    full = estimate_cost("anthropic", "claude-3-5-haiku-latest", TokenUsage(1_000_000, 0, 0))
    cached = estimate_cost(
        "anthropic", "claude-3-5-haiku-latest", TokenUsage(1_000_000, 0, 1_000_000)
    )
    assert full == 800_000
    assert cached == 80_000
    assert cached < full


def test_unknown_model_returns_zero_and_warns(monkeypatch: pytest.MonkeyPatch) -> None:
    warnings: list[str] = []

    def _capture(event: str, **_: object) -> None:
        warnings.append(event)

    monkeypatch.setattr(pricing.logger, "warning", _capture)
    cost = estimate_cost("acme-corp", "totally-unknown", TokenUsage(500, 500, 0))
    # Jamais bloquant (le metering ne doit pas casser un appel) : coût 0 + warning.
    assert cost == 0
    assert warnings == ["ai_pricing_unknown_model"]


def test_price_version_is_stamped() -> None:
    # `price_version` estampillé sur chaque événement pour recalcul/contestation (D4).
    assert isinstance(pricing.PRICE_VERSION, str)
    assert pricing.PRICE_VERSION
