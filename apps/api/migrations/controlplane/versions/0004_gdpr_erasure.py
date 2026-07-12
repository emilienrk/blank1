"""Effacement RGPD (Phase 4 T5) : état `pending_deletion`, horodatage de demande,
trace minimale `erasure_log`.

Revision ID: 0004_gdpr_erasure
Revises: 0003_migration_reports
Create Date: 2026-07-12

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_gdpr_erasure"
down_revision: str | None = "0003_migration_reports"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # `tenant_state` est stocké en VARCHAR (native_enum=False, sans CHECK) : ajouter
    # une valeur ne touche pas le schéma, seule la colonne suivante est nouvelle.
    op.add_column(
        "tenants",
        sa.Column("deletion_requested_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "erasure_log",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("slug", sa.String(length=40), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "executed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )


def downgrade() -> None:
    op.drop_table("erasure_log")
    op.drop_column("tenants", "deletion_requested_at")
