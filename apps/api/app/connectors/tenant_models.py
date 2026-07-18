"""Connexions OAuth tierces et subscriptions webhook — données du client,
scopées tenant (ADR 0001, Phase 5 T1, décision D1).

Les tokens sont chiffrés via `KeyProvider` (AES-256-GCM) : AUCUN token provider
en clair nulle part — ni en base, ni dans les logs, ni dans une réponse API
(invariant n°1 de la phase).
"""

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, LargeBinary, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.tenancy.tenant_base import TenantScoped


class ConnectorProvider(enum.StrEnum):
    GOOGLE = "google"
    MICROSOFT = "microsoft"


class ConnectionKind(enum.StrEnum):
    # Connexion partagée du tenant (compte de service humain, ex. contact@) ou
    # connexion personnelle d'un membre.
    TENANT = "tenant"
    USER = "user"


class ConnectionStatus(enum.StrEnum):
    ACTIVE = "active"
    # Refresh token invalidé (révocation côté provider, `invalid_grant`) : la SPA
    # propose le re-consentement guidé (§5).
    NEEDS_RECONSENT = "needs_reconsent"
    REVOKED = "revoked"
    ERROR = "error"


def _enum_values(enum_cls: type[enum.Enum]) -> list[str]:
    return [str(member.value) for member in enum_cls]


class ConnectorConnection(Base, TenantScoped):
    __tablename__ = "connector_connections"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    provider: Mapped[ConnectorProvider] = mapped_column(
        Enum(
            ConnectorProvider,
            name="connector_provider",
            native_enum=False,
            length=20,
            values_callable=_enum_values,
        )
    )
    kind: Mapped[ConnectionKind] = mapped_column(
        Enum(
            ConnectionKind,
            name="connection_kind",
            native_enum=False,
            length=10,
            values_callable=_enum_values,
        ),
        default=ConnectionKind.TENANT,
    )
    # UUID d'un user global (control-plane) — propriétaire si kind=user, sinon null.
    user_id: Mapped[uuid.UUID | None] = mapped_column(default=None)
    # Adresse du compte connecté (affichage SPA uniquement).
    account_label: Mapped[str] = mapped_column(String(320))
    scopes: Mapped[list[str]] = mapped_column(JSON(), default=list)
    # Tokens chiffrés KeyProvider — jamais en clair (invariant n°1 Phase 5).
    access_token_enc: Mapped[bytes] = mapped_column(LargeBinary())
    refresh_token_enc: Mapped[bytes] = mapped_column(LargeBinary())
    access_token_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[ConnectionStatus] = mapped_column(
        Enum(
            ConnectionStatus,
            name="connection_status",
            native_enum=False,
            length=20,
            values_callable=_enum_values,
        ),
        default=ConnectionStatus.ACTIVE,
    )
    # Résumé technique du dernier échec (jamais de token ni de contenu métier).
    last_error: Mapped[str | None] = mapped_column(Text(), default=None)
    # La santé dérive des opérations réelles (décision D8) : refresh, appels, webhooks.
    health_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ConnectorSubscription(Base, TenantScoped):
    # `tenant_id` en colonne propre (pas via jointure) : le filtre automatique est
    # par classe, chaque table scopée porte le sien.
    __tablename__ = "connector_subscriptions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("connector_connections.id", ondelete="CASCADE")
    )
    capability: Mapped[str] = mapped_column(String(20))
    # Identifiant de la subscription côté provider (subscription Graph, channel Google).
    provider_subscription_id: Mapped[str] = mapped_column(String(255))
    resource: Mapped[str] = mapped_column(String(255))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Secret aléatoire remis au provider et rejoué dans chaque notification —
    # stocké HACHÉ (sha256), comme tout token du socle (invariant n°3 Phase 5).
    client_state_hash: Mapped[str] = mapped_column(String(64))
    # Spécificités provider nécessaires au renouvellement (ex. resourceId Google).
    provider_data: Mapped[dict[str, Any]] = mapped_column(JSON(), default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
