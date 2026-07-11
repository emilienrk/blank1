"""Purge périodique des données d'auth expirées (Phase 2 T9)."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.auth.models import AuthSession, Invitation, LoginChallenge
from app.auth.tasks import purge_expired_auth_rows
from app.auth.tokens import generate_token, hash_token
from app.core.config import Settings
from app.core.db import get_control_sessionmaker
from tests.conftest import requires_postgres
from tests.helpers import add_catalog_tenant, create_user

pytestmark = requires_postgres


async def test_purge_removes_only_dead_rows(db_env: Settings) -> None:
    user = await create_user("alice@example.com")
    tenant = await add_catalog_tenant("acme")
    now = datetime.now(UTC)
    past, future = now - timedelta(hours=1), now + timedelta(hours=1)

    async with get_control_sessionmaker()() as session:
        session.add_all(
            [
                AuthSession(
                    user_id=user.id, token_hash=hash_token(generate_token()), expires_at=past
                ),
                AuthSession(
                    user_id=user.id,
                    token_hash=hash_token(generate_token()),
                    expires_at=future,
                    revoked_at=now,
                ),
                AuthSession(
                    user_id=user.id, token_hash=hash_token(generate_token()), expires_at=future
                ),
                LoginChallenge(
                    user_id=user.id, token_hash=hash_token(generate_token()), expires_at=past
                ),
                Invitation(
                    email="late@example.com",
                    tenant_id=tenant.id,
                    role="member",
                    token_hash=hash_token(generate_token()),
                    expires_at=past,
                ),
                Invitation(
                    email="soon@example.com",
                    tenant_id=tenant.id,
                    role="member",
                    token_hash=hash_token(generate_token()),
                    expires_at=future,
                ),
            ]
        )
        await session.commit()

    counts = await purge_expired_auth_rows()
    assert counts == {"sessions": 2, "login_challenges": 1, "invitations": 1}

    async with get_control_sessionmaker()() as session:
        remaining_sessions = await session.scalar(select(func.count()).select_from(AuthSession))
        remaining_invitations = await session.scalar(select(func.count()).select_from(Invitation))
        assert remaining_sessions == 1  # la session valide survit
        assert remaining_invitations == 1  # l'invitation encore valable survit
