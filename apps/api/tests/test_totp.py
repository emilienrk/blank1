# TestClient (starlette/httpx) expose des membres partiellement typés.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
"""TOTP : enrôlement, activation, login en deux temps, anti-rejeu, récupération
(Phase 2 T5, décision D4 : secrets chiffrés)."""

import time

import pyotp
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth.models import UserCredentials
from app.core.config import Settings
from app.core.db import get_control_sessionmaker
from app.main import create_app
from tests.conftest import requires_postgres
from tests.helpers import create_user, reset_db_engines

pytestmark = requires_postgres

PASSWORD = "correct-horse-battery-staple"
EMAIL = "alice@example.com"


def _login(client: TestClient, password: str = PASSWORD) -> dict[str, object]:
    response = client.post("/api/v1/auth/login", json={"email": EMAIL, "password": password})
    assert response.status_code == 200
    return response.json()


def _enroll(client: TestClient) -> tuple[str, list[str]]:
    """Setup + activation TOTP ; retourne (secret, codes de récupération)."""
    setup = client.post("/api/v1/auth/totp/setup")
    assert setup.status_code == 200
    secret = setup.json()["secret"]
    assert "otpauth://" in setup.json()["otpauth_uri"]

    bad = client.post("/api/v1/auth/totp/activate", json={"code": "000000"})
    assert bad.status_code == 400

    activate = client.post("/api/v1/auth/totp/activate", json={"code": pyotp.TOTP(secret).now()})
    assert activate.status_code == 200
    codes = activate.json()["recovery_codes"]
    assert len(codes) == 8
    return secret, codes


async def test_totp_full_flow_with_anti_replay_and_recovery(db_env: Settings) -> None:
    user = await create_user(EMAIL, PASSWORD)
    await reset_db_engines()

    with TestClient(create_app()) as client:
        _login(client)
        secret, recovery_codes = _enroll(client)

        # Le secret n'est JAMAIS stocké en clair (chiffré via KeyProvider).
        client.post("/api/v1/auth/logout")

        # Login en deux temps désormais : mot de passe → challenge → code.
        first_step = _login(client)
        assert first_step["status"] == "totp_required"
        challenge = first_step["challenge_token"]
        assert isinstance(challenge, str)

        # Code du pas de temps suivant (fenêtre ±1) : évite la collision avec le
        # compteur consommé à l'activation (anti-rejeu strictement croissant).
        totp = pyotp.TOTP(secret)
        next_code = totp.at(int(time.time()) + totp.interval)
        second_step = client.post(
            "/api/v1/auth/login/totp", json={"challenge_token": challenge, "code": next_code}
        )
        assert second_step.status_code == 200
        assert client.get("/api/v1/auth/me").json()["totp_enabled"] is True

        # Rejeu du même code sur un nouveau login → refusé.
        client.post("/api/v1/auth/logout")
        replay_step = _login(client)
        replay = client.post(
            "/api/v1/auth/login/totp",
            json={"challenge_token": replay_step["challenge_token"], "code": next_code},
        )
        assert replay.status_code == 401

        # Un challenge est à usage unique : rejouer le même challenge → refusé.
        reused_challenge = client.post(
            "/api/v1/auth/login/totp",
            json={"challenge_token": replay_step["challenge_token"], "code": totp.now()},
        )
        assert reused_challenge.status_code == 401

        # Code de récupération : usage unique.
        recovery_step = _login(client)
        recovered = client.post(
            "/api/v1/auth/login/totp",
            json={
                "challenge_token": recovery_step["challenge_token"],
                "code": recovery_codes[0],
            },
        )
        assert recovered.status_code == 200

        retry_step = _login(client)
        reused_recovery = client.post(
            "/api/v1/auth/login/totp",
            json={"challenge_token": retry_step["challenge_token"], "code": recovery_codes[0]},
        )
        assert reused_recovery.status_code == 401

    await reset_db_engines()
    async with get_control_sessionmaker()() as session:
        credentials = await session.scalar(
            select(UserCredentials).where(UserCredentials.user_id == user.id)
        )
        assert credentials is not None
        assert credentials.totp_secret_encrypted is not None
        assert secret.encode() not in credentials.totp_secret_encrypted


async def test_totp_disable_requires_password(db_env: Settings) -> None:
    await create_user(EMAIL, PASSWORD)
    await reset_db_engines()

    with TestClient(create_app()) as client:
        _login(client)
        _enroll(client)

        wrong = client.post("/api/v1/auth/totp/disable", json={"password": "mauvais"})
        assert wrong.status_code == 400

        ok = client.post("/api/v1/auth/totp/disable", json={"password": PASSWORD})
        assert ok.status_code == 200

        # Le login redevient un seul temps.
        client.post("/api/v1/auth/logout")
        assert _login(client)["status"] == "ok"


async def test_totp_setup_refused_when_already_enabled(db_env: Settings) -> None:
    await create_user(EMAIL, PASSWORD)
    await reset_db_engines()

    with TestClient(create_app()) as client:
        _login(client)
        _enroll(client)
        again = client.post("/api/v1/auth/totp/setup")
        assert again.status_code == 409
