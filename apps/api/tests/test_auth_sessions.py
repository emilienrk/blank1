# TestClient (starlette/httpx) expose des membres partiellement typés.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
"""Sessions serveur + login mot de passe (Phase 2 T4, décisions D1/D3)."""

from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth.models import AuthSession, UserCredentials
from app.auth.tokens import hash_token
from app.core.config import Settings
from app.core.db import get_control_sessionmaker
from app.main import create_app
from tests.conftest import requires_postgres
from tests.helpers import create_session_token, create_user, reset_db_engines

pytestmark = requires_postgres

PASSWORD = "correct-horse-battery-staple"


async def test_login_sets_cookie_and_stores_only_hash(db_env: Settings) -> None:
    user = await create_user("alice@example.com", PASSWORD)
    await reset_db_engines()

    with TestClient(create_app()) as client:
        response = client.post(
            "/api/v1/auth/login", json={"email": "alice@example.com", "password": PASSWORD}
        )
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "challenge_token": None}
        token = response.cookies.get(db_env.session_cookie_name)
        assert token

        me = client.get("/api/v1/auth/me")
        assert me.status_code == 200
        assert me.json()["email"] == "alice@example.com"

    await reset_db_engines()
    async with get_control_sessionmaker()() as session:
        stored = await session.scalar(select(AuthSession).where(AuthSession.user_id == user.id))
        assert stored is not None
        assert stored.token_hash != token  # jamais le token en clair
        assert stored.token_hash == hash_token(token)


async def test_login_failures_are_indistinct(db_env: Settings) -> None:
    await create_user("alice@example.com", PASSWORD)
    await reset_db_engines()

    with TestClient(create_app()) as client:
        wrong_password = client.post(
            "/api/v1/auth/login", json={"email": "alice@example.com", "password": "mauvais"}
        )
        unknown_email = client.post(
            "/api/v1/auth/login", json={"email": "inconnu@example.com", "password": "mauvais"}
        )
    assert wrong_password.status_code == unknown_email.status_code == 401
    assert wrong_password.json() == unknown_email.json()


async def test_me_requires_authentication(db_env: Settings) -> None:
    await reset_db_engines()
    with TestClient(create_app()) as client:
        assert client.get("/api/v1/auth/me").status_code == 401
        client.cookies.set(db_env.session_cookie_name, "token-bidon")
        assert client.get("/api/v1/auth/me").status_code == 401


async def test_expired_session_is_rejected(db_env: Settings) -> None:
    user = await create_user("alice@example.com", PASSWORD)
    token = await create_session_token(user.id)
    async with get_control_sessionmaker()() as session:
        stored = await session.scalar(select(AuthSession).where(AuthSession.user_id == user.id))
        assert stored is not None
        stored.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        await session.commit()
    await reset_db_engines()

    with TestClient(create_app()) as client:
        client.cookies.set(db_env.session_cookie_name, token)
        assert client.get("/api/v1/auth/me").status_code == 401


async def test_logout_revokes_session(db_env: Settings) -> None:
    user = await create_user("alice@example.com", PASSWORD)
    await reset_db_engines()

    with TestClient(create_app()) as client:
        client.post("/api/v1/auth/login", json={"email": "alice@example.com", "password": PASSWORD})
        assert client.get("/api/v1/auth/me").status_code == 200
        assert client.post("/api/v1/auth/logout").status_code == 200
        assert client.get("/api/v1/auth/me").status_code == 401

    await reset_db_engines()
    async with get_control_sessionmaker()() as session:
        stored = await session.scalar(select(AuthSession).where(AuthSession.user_id == user.id))
        assert stored is not None
        assert stored.revoked_at is not None


async def test_login_rehashes_outdated_password_hash(db_env: Settings) -> None:
    user = await create_user("alice@example.com", PASSWORD)
    # Hash avec des paramètres plus faibles que la config courante → re-hash attendu.
    weak_hash = PasswordHasher(time_cost=1).hash(PASSWORD)
    async with get_control_sessionmaker()() as session:
        credentials = await session.get(UserCredentials, user.id)
        assert credentials is not None
        credentials.password_hash = weak_hash
        await session.commit()
    await reset_db_engines()

    with TestClient(create_app()) as client:
        response = client.post(
            "/api/v1/auth/login", json={"email": "alice@example.com", "password": PASSWORD}
        )
        assert response.status_code == 200

    await reset_db_engines()
    async with get_control_sessionmaker()() as session:
        credentials = await session.get(UserCredentials, user.id)
        assert credentials is not None
        assert credentials.password_hash != weak_hash


async def test_csrf_origin_check_blocks_foreign_origin(db_env: Settings) -> None:
    await create_user("alice@example.com", PASSWORD)
    await reset_db_engines()

    with TestClient(create_app()) as client:
        blocked = client.post(
            "/api/v1/auth/login",
            json={"email": "alice@example.com", "password": PASSWORD},
            headers={"origin": "https://evil.example.org", "host": "acme.app.example.fr"},
        )
        assert blocked.status_code == 403

        same_host = client.post(
            "/api/v1/auth/login",
            json={"email": "alice@example.com", "password": PASSWORD},
            headers={"origin": "https://acme.app.example.fr", "host": "acme.app.example.fr"},
        )
        assert same_host.status_code == 200
