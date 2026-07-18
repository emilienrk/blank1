"""Simplification MVP (ADR 0002/0003) : soft-delete des tenants, retrait du RGPD à
délai de grâce et du back-office. Révision TEMPORAIRE — l'étape 3 de la
simplification remplace les deux arbres Alembic par une baseline unique.

Revision ID: 0008_mvp_soft_delete
Revises: 0007_tenant_modules
Create Date: 2026-07-18

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_mvp_soft_delete"
down_revision: str | None = "0007_tenant_modules"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tenants", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.drop_column("tenants", "deletion_requested_at")
    op.drop_table("erasure_log")
    # Back-office archivé (ADR 0003).
    op.drop_column("users", "is_platform_admin")
    op.drop_table("migration_reports")


def downgrade() -> None:
    raise NotImplementedError("Réintroduction via le tag archive/pre-mvp-simplification")
