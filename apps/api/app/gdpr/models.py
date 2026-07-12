"""Trace minimale de l'effacement RGPD (control-plane, Phase 4 T5).

Écrite après le `DROP DATABASE` définitif : preuve que l'effacement a eu lieu,
sans aucune donnée métier (juste le nécessaire pour répondre à un contrôle).
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import ControlPlaneBase


class ErasureLog(ControlPlaneBase):
    __tablename__ = "erasure_log"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(40))
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    executed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
