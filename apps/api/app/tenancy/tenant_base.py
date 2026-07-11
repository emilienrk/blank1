"""Base déclarative du schéma TENANT — MetaData strictement séparée du control-plane.

Toute table métier vit ici (une base de données par tenant). Aucune table de
catalogue dans ce schéma, aucune table métier dans le control-plane (invariant I3).
"""

from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class TenantBase(DeclarativeBase):
    """Base déclarative des tables présentes dans chaque DB tenant."""


class TenantSetting(TenantBase):
    """Clé/valeur par tenant — première table du schéma, prouve les migrations et le seed."""

    __tablename__ = "tenant_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
