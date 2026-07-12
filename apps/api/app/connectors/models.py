"""Routage des webhooks entrants — control-plane, ROUTAGE UNIQUEMENT (décision D6).

Un webhook arrive sur l'apex sans sous-domaine ni session : il faut retrouver le
tenant AVANT de pouvoir ouvrir sa DB. Cette table ne contient que des
identifiants (aucune donnée métier, aucun token — invariant §3 du plan global).
Le `route_key` opaque figure dans l'URL de webhook et évite d'exposer des ids
internes ; l'authentification du contenu repose sur le `client_state` haché en
DB tenant.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import ControlPlaneBase


class WebhookRoute(ControlPlaneBase):
    __tablename__ = "webhook_routes"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    route_key: Mapped[str] = mapped_column(String(64), unique=True)
    provider: Mapped[str] = mapped_column(String(20))
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    # UUID d'une connector_connection (DB tenant) — pas de FK inter-bases.
    connection_id: Mapped[uuid.UUID] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
