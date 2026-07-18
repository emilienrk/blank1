"""Dépendances FastAPI d'authentification (Phase 2 T4/T7).

`current_auth` lit le cookie de session, valide la session serveur et fournit
user + session.
"""

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import AuthSession
from app.auth.service import resolve_session
from app.core.config import get_settings
from app.core.db import get_control_session
from app.directory.models import User


@dataclass(frozen=True, slots=True)
class CurrentAuth:
    user: User
    session: AuthSession


async def current_auth_or_none(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_control_session)],
) -> CurrentAuth | None:
    token = request.cookies.get(get_settings().session_cookie_name)
    if not token:
        return None
    resolved = await resolve_session(session, token)
    if resolved is None:
        return None
    auth_session, user = resolved
    await session.commit()  # persiste last_seen_at
    return CurrentAuth(user=user, session=auth_session)


async def current_auth(
    auth: Annotated[CurrentAuth | None, Depends(current_auth_or_none)],
) -> CurrentAuth:
    if auth is None:
        raise HTTPException(status_code=401, detail="Authentification requise")
    return auth


async def current_user(auth: Annotated[CurrentAuth, Depends(current_auth)]) -> User:
    return auth.user
