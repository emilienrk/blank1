"""Table de prix versionnée — EN CODE, versionnée par le repo (décision D4).

Les prix providers changent par release, pas par action utilisateur : un fichier
Python typé, revu en PR, suffit et reste auditable. `PRICE_VERSION` est estampillé
sur chaque `ai_usage_events` : recalcul/contestation a posteriori possibles.

Les prix sont en **micro-euros par million de tokens** (entiers, pas de flottant en
base). Un couple (provider, modèle) inconnu → coût 0 + warning loggé, JAMAIS bloquant :
le metering ne doit pas casser un appel IA (invariant : chaque appel est mesuré, même
approximativement).
"""

from dataclasses import dataclass

import structlog

logger = structlog.get_logger()

# Date de version de la grille — à bumper à chaque changement de tarif provider
# (réflexe au runbook Phase 8, risque F3 du plan).
PRICE_VERSION = "2026-07-12"


@dataclass(frozen=True, slots=True)
class ModelPrice:
    """Prix en micro-euros par MILLION de tokens (in / out / cachés)."""

    input_per_mtok: int
    output_per_mtok: int
    cached_input_per_mtok: int


@dataclass(frozen=True, slots=True)
class TokenUsage:
    input_tokens: int
    output_tokens: int
    cached_tokens: int = 0


# Grille (provider, modèle) → prix. Micro-euros/Mtok. Valeurs indicatives,
# tenues à jour par PR (décision D4). Les tokens cachés sont facturés au tarif
# réduit du provider quand il existe, sinon au tarif input.
PRICES: dict[tuple[str, str], ModelPrice] = {
    # Mistral (France, ZDR) — provider par défaut.
    ("mistral", "mistral-small-latest"): ModelPrice(200_000, 600_000, 200_000),
    ("mistral", "mistral-large-latest"): ModelPrice(2_000_000, 6_000_000, 2_000_000),
    ("mistral", "mistral-embed"): ModelPrice(100_000, 0, 100_000),
    # Anthropic (hors UE, DPA + SCC).
    ("anthropic", "claude-3-5-haiku-latest"): ModelPrice(800_000, 4_000_000, 80_000),
    ("anthropic", "claude-3-5-sonnet-latest"): ModelPrice(3_000_000, 15_000_000, 300_000),
    # OpenAI (hors UE, DPA + SCC).
    ("openai", "gpt-4o-mini"): ModelPrice(150_000, 600_000, 75_000),
    ("openai", "gpt-4o"): ModelPrice(2_500_000, 10_000_000, 1_250_000),
    ("openai", "text-embedding-3-small"): ModelPrice(20_000, 0, 20_000),
}


def estimate_cost(provider: str, model: str, usage: TokenUsage) -> int:
    """Coût estimé en micro-euros. Modèle inconnu → 0 + warning (jamais bloquant)."""
    price = PRICES.get((provider, model))
    if price is None:
        logger.warning("ai_pricing_unknown_model", provider=provider, model=model)
        return 0
    billable_input = max(usage.input_tokens - usage.cached_tokens, 0)
    total = (
        billable_input * price.input_per_mtok
        + usage.cached_tokens * price.cached_input_per_mtok
        + usage.output_tokens * price.output_per_mtok
    )
    # Division entière en fin de calcul : les prix sont par million de tokens.
    return total // 1_000_000
