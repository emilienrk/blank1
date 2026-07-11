# TestClient (starlette/httpx) expose des membres partiellement typés.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
"""Rate limiting des endpoints d'auth (Phase 2 T9, décision D9)."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.main import create_app
from tests.conftest import requires_postgres
from tests.helpers import reset_db_engines

pytestmark = requires_postgres


async def test_login_rate_limited_then_window_resets(
    db_env: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTH_RATE_LIMIT_ATTEMPTS", "3")
    monkeypatch.setenv("AUTH_RATE_LIMIT_WINDOW_SECONDS", "1")
    get_settings.cache_clear()
    await reset_db_engines()

    payload = {"email": "brute@example.com", "password": "mauvais"}
    with TestClient(create_app()) as client:
        for _ in range(3):
            assert client.post("/api/v1/auth/login", json=payload).status_code == 401
        blocked = client.post("/api/v1/auth/login", json=payload)
        assert blocked.status_code == 429

        # Fenêtre fixe : après expiration, les tentatives reprennent.
        await asyncio.to_thread(_sleep_seconds, 1.2)
        assert client.post("/api/v1/auth/login", json=payload).status_code == 401


def _sleep_seconds(seconds: float) -> None:
    import time

    time.sleep(seconds)


async def test_totp_login_and_invitation_accept_rate_limited(
    db_env: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTH_RATE_LIMIT_ATTEMPTS", "2")
    get_settings.cache_clear()
    await reset_db_engines()

    with TestClient(create_app()) as client:
        for _ in range(2):
            response = client.post(
                "/api/v1/auth/login/totp",
                json={"challenge_token": "bidon", "code": "000000"},
            )
            assert response.status_code == 401
        assert (
            client.post(
                "/api/v1/auth/login/totp",
                json={"challenge_token": "bidon", "code": "000000"},
            ).status_code
            == 429
        )

        for _ in range(2):
            assert (
                client.post("/api/v1/auth/invitations/accept", json={"token": "bidon"}).status_code
                == 400
            )
        assert (
            client.post("/api/v1/auth/invitations/accept", json={"token": "bidon"}).status_code
            == 429
        )
