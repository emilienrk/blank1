"""Rapport de migrations persisté pour le back-office (Phase 3 T6, décision D6).

Revision ID: 0003_migration_reports
Revises: 0002_auth
Create Date: 2026-07-12

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_migration_reports"
down_revision: str | None = "0002_auth"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "migration_reports",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "status",
            sa.Enum(
                "running",
                "done",
                "failed",
                name="migration_run_status",
                native_enum=False,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("outcomes", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("migration_reports")
