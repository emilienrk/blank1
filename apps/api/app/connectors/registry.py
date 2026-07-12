"""Registre des providers de connecteurs (Phase 5 T2).

Registre EN CODE (dict figé — pas de plugin dynamique) : chaque provider est
décrit par un `ProviderManifest` typé (scopes par capability, endpoints OAuth,
spécificités webhooks). Les tests remplacent des manifests entiers via
`override_provider` pour pointer sur des serveurs locaux.
"""

from collections.abc import Callable, Generator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from app.connectors.tenant_models import ConnectorProvider

CAPABILITY_MAIL = "mail"
CAPABILITY_CALENDAR = "calendar"

KNOWN_CAPABILITIES: frozenset[str] = frozenset({CAPABILITY_MAIL, CAPABILITY_CALENDAR})


class UnknownProviderError(ValueError):
    """Provider absent du registre."""


class UnsupportedCapabilityError(ValueError):
    """Capability non supportée par le provider (ou inconnue du registre)."""


@dataclass(frozen=True, slots=True)
class ProviderManifest:
    provider: ConnectorProvider
    authorization_endpoint: str
    token_endpoint: str
    # Endpoint interrogé au callback pour libeller le compte connecté (email).
    account_info_endpoint: str
    # Extrait le label du compte de la réponse `account_info_endpoint`.
    parse_account_label: Callable[[dict[str, Any]], str | None]
    # Scopes toujours demandés (identité du compte, offline chez Microsoft).
    base_scopes: tuple[str, ...]
    capability_scopes: Mapping[str, tuple[str, ...]]
    # Base des APIs métier (Graph, googleapis) — surchargée par les tests.
    api_base_url: str
    # Endpoint de révocation (best-effort, décision D9) — None si inexistant.
    revoke_endpoint: str | None
    # Paramètres additionnels de l'URL d'autorisation (access_type/prompt Google).
    authorization_extra_params: Mapping[str, str]
    # Budget local d'appels par connexion (T7), aligné sur les quotas publics.
    requests_per_minute: int
    # Durée de vie des subscriptions webhook côté provider (T8).
    subscription_ttl_hours: int

    @property
    def capabilities(self) -> frozenset[str]:
        return frozenset(self.capability_scopes)

    def scopes_for(self, capabilities: list[str]) -> list[str]:
        """Scopes à demander pour un ensemble de capabilities (base + union)."""
        scopes = list(self.base_scopes)
        for capability in capabilities:
            try:
                capability_scopes = self.capability_scopes[capability]
            except KeyError:
                msg = f"Capability {capability!r} non supportée par {self.provider.value}."
                raise UnsupportedCapabilityError(msg) from None
            scopes.extend(scope for scope in capability_scopes if scope not in scopes)
        return scopes


def _register() -> dict[ConnectorProvider, ProviderManifest]:
    # Import local : les manifests importent ProviderManifest d'ici.
    from app.connectors.providers.google.manifest import GOOGLE_MANIFEST
    from app.connectors.providers.microsoft.manifest import MICROSOFT_MANIFEST

    return {
        ConnectorProvider.GOOGLE: GOOGLE_MANIFEST,
        ConnectorProvider.MICROSOFT: MICROSOFT_MANIFEST,
    }


_registry: dict[ConnectorProvider, ProviderManifest] | None = None


def get_provider(provider: ConnectorProvider | str) -> ProviderManifest:
    global _registry
    if _registry is None:
        _registry = _register()
    try:
        key = ConnectorProvider(provider)
    except ValueError:
        msg = f"Provider de connecteur inconnu : {provider!r}"
        raise UnknownProviderError(msg) from None
    return _registry[key]


@contextmanager
def override_provider(manifest: ProviderManifest) -> Generator[None]:
    """Substitue un manifest (tests : endpoints locaux) pour la durée du bloc."""
    global _registry
    if _registry is None:
        _registry = _register()
    previous = _registry[manifest.provider]
    _registry[manifest.provider] = manifest
    try:
        yield
    finally:
        _registry[manifest.provider] = previous
