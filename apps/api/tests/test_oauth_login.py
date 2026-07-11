# TestClient/httpx/authlib exposent des membres partiellement typés.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportMissingTypeStubs=false, reportUnknownArgumentType=false
"""OAuth login Google/Microsoft (Phase 2 T6, décision D5 : invitation only).

Faux provider OIDC local : métadonnées, endpoint token et JWKS servis par un
transport httpx mocké ; id_token RS256 signé par une clé de test.
"""

import time
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from authlib.jose import JsonWebKey, jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import select

import app.auth.oauth as oauth_module
from app.auth.models import OAuthIdentity, OAuthProvider
from app.auth.tokens import verify_payload
from app.core.config import Settings, get_settings
from app.core.db import get_control_sessionmaker
from app.main import create_app
from tests.conftest import requires_postgres
from tests.helpers import create_user, reset_db_engines

pytestmark = requires_postgres

CLIENT_ID = "test-client-id"
TOKEN_ENDPOINT = "https://fake.google.example/token"
JWKS_URI = "https://fake.google.example/jwks"
AUTHORIZATION_ENDPOINT = "https://fake.google.example/authorize"

_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_private_pem = _private_key.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
)
_public_pem = _private_key.public_key().public_bytes(
    serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
)
_jwk = JsonWebKey.import_key(_public_pem, {"kty": "RSA", "kid": "test-key", "use": "sig"})
JWKS = {"keys": [_jwk.as_dict()]}


def make_id_token(
    *,
    sub: str = "google-sub-1",
    email: str = "alice@example.com",
    email_verified: bool = True,
    nonce: str = "",
    aud: str = CLIENT_ID,
) -> str:
    now = int(time.time())
    payload: dict[str, Any] = {
        "iss": "https://fake.google.example",
        "aud": aud,
        "sub": sub,
        "email": email,
        "email_verified": email_verified,
        "iat": now,
        "exp": now + 600,
        "nonce": nonce,
    }
    token: bytes = jwt.encode({"alg": "RS256", "kid": "test-key"}, payload, _private_pem)
    return token.decode()


@pytest.fixture
def fake_oidc(db_env: Settings, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Provider OIDC simulé + credentials client configurés ; retourne le holder
    mutable dont `id_token` est renvoyé par l'endpoint token."""
    monkeypatch.setenv("GOOGLE_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-secret")
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://app.example.fr")
    get_settings.cache_clear()

    holder: dict[str, str] = {"id_token": ""}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == oauth_module.GOOGLE_METADATA_URL:
            return httpx.Response(
                200,
                json={
                    "authorization_endpoint": AUTHORIZATION_ENDPOINT,
                    "token_endpoint": TOKEN_ENDPOINT,
                    "jwks_uri": JWKS_URI,
                },
            )
        if url == TOKEN_ENDPOINT:
            return httpx.Response(200, json={"id_token": holder["id_token"]})
        if url == JWKS_URI:
            return httpx.Response(200, json=JWKS)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        oauth_module, "_http_client_factory", lambda: httpx.AsyncClient(transport=transport)
    )
    return holder


def _start_login(client: TestClient) -> tuple[str, str]:
    """GET /oauth/google/start → (state, nonce)."""
    response = client.get(
        "/api/v1/auth/oauth/google/start",
        headers={"host": "acme.app.example.fr"},
        follow_redirects=False,
    )
    assert response.status_code == 307
    location = response.headers["location"]
    assert location.startswith(AUTHORIZATION_ENDPOINT)
    query = parse_qs(urlsplit(location).query)
    assert query["redirect_uri"] == ["http://app.example.fr/api/v1/auth/oauth/google/callback"]
    state = query["state"][0]
    nonce = verify_payload(state)["n"]
    return state, nonce


async def test_oauth_login_links_invited_user_and_sets_session(
    fake_oidc: dict[str, str], db_env: Settings
) -> None:
    user = await create_user("alice@example.com")  # compte invité, sans mot de passe
    await reset_db_engines()

    with TestClient(create_app()) as client:
        state, nonce = _start_login(client)
        fake_oidc["id_token"] = make_id_token(nonce=nonce)
        callback = client.get(
            f"/api/v1/auth/oauth/google/callback?code=fake-code&state={state}",
            follow_redirects=False,
        )
        assert callback.status_code == 303
        # Retour vers le sous-domaine d'origine, session posée.
        assert callback.headers["location"] == "http://acme.app.example.fr/"
        assert client.get("/api/v1/auth/me").status_code == 200

        # Deuxième login : résolu par (provider, subject), même avec un autre email.
        client.post("/api/v1/auth/logout")
        state2, nonce2 = _start_login(client)
        fake_oidc["id_token"] = make_id_token(nonce=nonce2, email="autre@example.com")
        second = client.get(
            f"/api/v1/auth/oauth/google/callback?code=fake-code&state={state2}",
            follow_redirects=False,
        )
        assert second.status_code == 303
        assert client.get("/api/v1/auth/me").json()["email"] == "alice@example.com"

    await reset_db_engines()
    async with get_control_sessionmaker()() as session:
        identity = await session.scalar(
            select(OAuthIdentity).where(OAuthIdentity.user_id == user.id)
        )
        assert identity is not None
        assert identity.provider is OAuthProvider.GOOGLE
        assert identity.subject == "google-sub-1"


async def test_oauth_login_refuses_unknown_email_without_creating_account(
    fake_oidc: dict[str, str], db_env: Settings
) -> None:
    await reset_db_engines()
    with TestClient(create_app()) as client:
        state, nonce = _start_login(client)
        fake_oidc["id_token"] = make_id_token(nonce=nonce, email="inconnu@example.com")
        callback = client.get(
            f"/api/v1/auth/oauth/google/callback?code=fake-code&state={state}",
            follow_redirects=False,
        )
        assert callback.status_code == 403
        assert client.get("/api/v1/auth/me").status_code == 401

    await reset_db_engines()
    async with get_control_sessionmaker()() as session:
        from app.directory.models import User

        assert (await session.scalar(select(User))) is None  # aucune création à la volée


async def test_oauth_login_refuses_unverified_email(
    fake_oidc: dict[str, str], db_env: Settings
) -> None:
    await create_user("alice@example.com")
    await reset_db_engines()
    with TestClient(create_app()) as client:
        state, nonce = _start_login(client)
        fake_oidc["id_token"] = make_id_token(nonce=nonce, email_verified=False)
        callback = client.get(
            f"/api/v1/auth/oauth/google/callback?code=fake-code&state={state}",
            follow_redirects=False,
        )
        assert callback.status_code == 403


async def test_oauth_callback_rejects_tampered_state(
    fake_oidc: dict[str, str], db_env: Settings
) -> None:
    await reset_db_engines()
    with TestClient(create_app()) as client:
        state, nonce = _start_login(client)
        fake_oidc["id_token"] = make_id_token(nonce=nonce)
        tampered = state[:-4] + "AAAA"
        callback = client.get(
            f"/api/v1/auth/oauth/google/callback?code=fake-code&state={tampered}",
            follow_redirects=False,
        )
        assert callback.status_code == 403


async def test_oauth_callback_rejects_wrong_nonce(
    fake_oidc: dict[str, str], db_env: Settings
) -> None:
    await create_user("alice@example.com")
    await reset_db_engines()
    with TestClient(create_app()) as client:
        state, _ = _start_login(client)
        fake_oidc["id_token"] = make_id_token(nonce="autre-nonce")
        callback = client.get(
            f"/api/v1/auth/oauth/google/callback?code=fake-code&state={state}",
            follow_redirects=False,
        )
        assert callback.status_code == 403
