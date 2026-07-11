"""Tâches Celery d'entretien de l'auth (Phase 2 T9).

Purges périodiques (beat) : sessions expirées/révoquées, jetons de login
partiels et invitations expirées. Les lignes purgées sont mortes
fonctionnellement — la purge borne juste la taille des tables.
"""

# Celery n'expose pas de types (voir app/worker.py).
# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUntypedFunctionDecorator=false, reportUnknownVariableType=false

import asyncio
from datetime import UTC, datetime
from typing import Any, cast

import structlog
from celery import shared_task
from sqlalchemy import CursorResult, delete, or_

from app.auth.models import AuthSession, Invitation, LoginChallenge
from app.core.db import dispose_control_engine, get_control_sessionmaker

logger = structlog.get_logger()


async def purge_expired_auth_rows() -> dict[str, int]:
    """Supprime sessions expirées/révoquées, challenges et invitations expirés."""
    now = datetime.now(UTC)
    async with get_control_sessionmaker()() as session:
        sessions_result = await session.execute(
            delete(AuthSession).where(
                or_(AuthSession.expires_at < now, AuthSession.revoked_at.is_not(None))
            )
        )
        challenges_result = await session.execute(
            delete(LoginChallenge).where(LoginChallenge.expires_at < now)
        )
        invitations_result = await session.execute(
            delete(Invitation).where(Invitation.expires_at < now, Invitation.accepted_at.is_(None))
        )
        await session.commit()
    counts = {
        "sessions": max(cast(CursorResult[Any], sessions_result).rowcount, 0),
        "login_challenges": max(cast(CursorResult[Any], challenges_result).rowcount, 0),
        "invitations": max(cast(CursorResult[Any], invitations_result).rowcount, 0),
    }
    logger.info("auth_purge_done", **counts)
    return counts


async def _run_purge() -> dict[str, int]:
    try:
        return await purge_expired_auth_rows()
    finally:
        # Pools asyncpg liés à leur event loop : fermer dans la même boucle (cf. app/cli.py).
        await dispose_control_engine()


@shared_task(name="core.auth.purge_expired")
def purge_expired() -> dict[str, int]:
    return asyncio.run(_run_purge())
