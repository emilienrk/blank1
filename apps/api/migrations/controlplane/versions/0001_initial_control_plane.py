"""Schéma control-plane initial : catalogue tenants, identités globales, memberships.

Revision ID: 0001_controlplane
Revises:
Create Date: 2026-07-11

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_controlplane"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("slug", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("db_name", sa.String(length=63), nullable=False),
        sa.Column("db_host", sa.String(length=255), nullable=False, server_default="default"),
        sa.Column(
            "state",
            sa.Enum(
                "provisioning",
                "active",
                "suspended",
                "failed",
                name="tenant_state",
                native_enum=False,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("plan", sa.String(length=50), nullable=False, server_default="standard"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("slug", name="ux_tenants_slug"),
        sa.UniqueConstraint("db_name", name="ux_tenants_db_name"),
    )

    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ux_users_email_lower", "users", [sa.text("lower(email)")], unique=True)

    op.create_table(
        "memberships",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(length=50), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("user_id", "tenant_id", name="ux_memberships_user_tenant"),
    )


def downgrade() -> None:
    op.drop_table("memberships")
    op.drop_index("ux_users_email_lower", table_name="users")
    op.drop_table("users")
    op.drop_table("tenants")
