"""Module d'exemple sample_digest (Phase 7 T6) : table sample_digest_digests en DB
tenant (décision D5 : préfixe `<name>_`, arbre tenant unique). Un module désactivé
garde sa table — le schéma tenant reste identique pour tous les tenants.

Revision ID: 0005_tenant_sample_digest
Revises: 0004_tenant_connectors
Create Date: 2026-07-13

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_tenant_sample_digest"
down_revision: str | None = "0004_tenant_connectors"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sample_digest_digests",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("message_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("summary", sa.Text(), nullable=False),
    )
    op.create_index(
        "ix_sample_digest_digests_generated_at", "sample_digest_digests", ["generated_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_sample_digest_digests_generated_at", table_name="sample_digest_digests")
    op.drop_table("sample_digest_digests")
