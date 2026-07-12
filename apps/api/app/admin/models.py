"""Rapport de migrations persisté (Phase 3 T6, décision D6).

Le back-office déclenche le runner de façon asynchrone (Celery) : ce rapport
donne à `GET /admin/migrations/last-report` de quoi répondre sans rejouer le
runner ni bloquer la requête HTTP sur sa durée (le runner peut itérer sur de
nombreuses bases).
"""

import enum
import uuid
from datetime import datetime
from typing import TypedDict

from sqlalchemy import JSON, DateTime, Enum, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import ControlPlaneBase


class MigrationRunStatus(enum.StrEnum):
    RUNNING = "running"
    DONE = "done"
    # Échec du runner lui-même (verrou advisory occupé) — distinct d'un échec
    # partiel sur une base, qui est reflété dans `outcomes` avec `status=done`.
    FAILED = "failed"


class MigrationOutcomeDict(TypedDict):
    database: str
    target: str
    ok: bool
    revision: str | None
    error: str | None


def _enum_values(enum_cls: type[enum.Enum]) -> list[str]:
    return [str(member.value) for member in enum_cls]


class MigrationReportRecord(ControlPlaneBase):
    __tablename__ = "migration_reports"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    status: Mapped[MigrationRunStatus] = mapped_column(
        Enum(
            MigrationRunStatus,
            name="migration_run_status",
            native_enum=False,
            length=20,
            values_callable=_enum_values,
        ),
        default=MigrationRunStatus.RUNNING,
    )
    summary: Mapped[str | None] = mapped_column(Text(), default=None)
    error: Mapped[str | None] = mapped_column(Text(), default=None)
    outcomes: Mapped[list[MigrationOutcomeDict]] = mapped_column(JSON(), default=list)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
