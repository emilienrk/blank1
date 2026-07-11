"""Auth Phase 2 : credentials, sessions, identités OAuth, invitations, codes de
récupération, jetons de login partiel ; users.display_name + is_platform_admin.

Revision ID: 0002_auth
Revises: 0001_controlplane
Create Date: 2026-07-11

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_auth"
down_revision: str | None = "0001_controlplane"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("display_name", sa.String(length=255), nullable=True))
    op.add_column(
        "users",
        sa.Column("is_platform_admin", sa.Boolean(), nullable=False, server_default="false"),
    )

    op.create_table(
        "user_credentials",
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("password_hash", sa.Text(), nullable=True),
        sa.Column("totp_secret_encrypted", sa.LargeBinary(), nullable=True),
        sa.Column("totp_enabled", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("totp_last_counter", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )

    op.create_table(
        "auth_recovery_codes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("code_hash", name="ux_auth_recovery_codes_code_hash"),
    )
    op.create_index("ix_auth_recovery_codes_user_id", "auth_recovery_codes", ["user_id"])

    op.create_table(
        "sessions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("token_hash", name="ux_sessions_token_hash"),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])

    op.create_table(
        "login_challenges",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("token_hash", name="ux_login_challenges_token_hash"),
    )

    op.create_table(
        "oauth_identities",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "provider",
            sa.Enum("google", "microsoft", name="oauth_provider", native_enum=False, length=20),
            nullable=False,
        ),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("provider", "subject", name="ux_oauth_identities_provider_subject"),
        sa.UniqueConstraint("provider", "user_id", name="ux_oauth_identities_provider_user"),
    )

    op.create_table(
        "invitations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column(
            "tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("role", sa.String(length=50), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "invited_by_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("token_hash", name="ux_invitations_token_hash"),
    )
    op.create_index("ix_invitations_tenant_id", "invitations", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_invitations_tenant_id", table_name="invitations")
    op.drop_table("invitations")
    op.drop_table("oauth_identities")
    op.drop_table("login_challenges")
    op.drop_index("ix_sessions_user_id", table_name="sessions")
    op.drop_table("sessions")
    op.drop_index("ix_auth_recovery_codes_user_id", table_name="auth_recovery_codes")
    op.drop_table("auth_recovery_codes")
    op.drop_table("user_credentials")
    op.drop_column("users", "is_platform_admin")
    op.drop_column("users", "display_name")
