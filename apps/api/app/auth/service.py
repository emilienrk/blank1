"""Logique d'authentification : mots de passe, sessions serveur, TOTP.

Décisions Phase 2 : argon2id avec re-hash transparent (D3), sessions en DB
control-plane avec token opaque haché (D1), secrets TOTP chiffrés via le
KeyProvider (D4). Les réponses d'échec sont indistinctes — jamais d'oracle
« email inconnu » vs « mauvais mot de passe ».
"""

import secrets
import uuid
from datetime import UTC, datetime, timedelta

import pyotp
import structlog
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import AuthSession, LoginChallenge, RecoveryCode, UserCredentials
from app.auth.tokens import generate_token, hash_token
from app.core.config import get_settings
from app.core.crypto import get_key_provider
from app.directory.models import User

logger = structlog.get_logger()

_hasher = PasswordHasher()

# Hash factice vérifié quand l'email est inconnu : égalise le temps de réponse.
_DUMMY_HASH = _hasher.hash("dummy-password-for-timing")

TOTP_ISSUER = "Socle SaaS"
RECOVERY_CODE_COUNT = 8


class AuthError(RuntimeError):
    """Échec d'authentification ou d'opération d'auth (message non oraculaire)."""


def _now() -> datetime:
    return datetime.now(UTC)


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> tuple[bool, bool]:
    """Retourne (valide, re-hash nécessaire)."""
    try:
        _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False, False
    return True, _hasher.check_needs_rehash(password_hash)


async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    return await session.scalar(select(User).where(func.lower(User.email) == email.lower()))


async def get_credentials(session: AsyncSession, user_id: uuid.UUID) -> UserCredentials | None:
    return await session.get(UserCredentials, user_id)


async def set_password(session: AsyncSession, user_id: uuid.UUID, password: str) -> None:
    """Pose ou remplace le mot de passe (crée la ligne credentials au besoin)."""
    credentials = await session.get(UserCredentials, user_id)
    if credentials is None:
        credentials = UserCredentials(user_id=user_id)
        session.add(credentials)
    credentials.password_hash = hash_password(password)


async def authenticate(
    session: AsyncSession, email: str, password: str
) -> tuple[User, UserCredentials] | None:
    """Vérifie email + mot de passe ; None sur tout échec (réponse indistincte)."""
    user = await get_user_by_email(session, email)
    credentials = await get_credentials(session, user.id) if user else None
    if user is None or credentials is None or credentials.password_hash is None:
        # Égalise le temps de réponse avec une vérification factice.
        verify_password(_DUMMY_HASH, password)
        return None
    valid, needs_rehash = verify_password(credentials.password_hash, password)
    if not valid:
        return None
    if needs_rehash:
        credentials.password_hash = hash_password(password)
    return user, credentials


# --- Sessions serveur (décision D1) ---


async def create_session(session: AsyncSession, user_id: uuid.UUID) -> str:
    """Crée une session et retourne le token en clair — sa seule apparition."""
    token = generate_token()
    ttl = timedelta(hours=get_settings().session_ttl_hours)
    session.add(AuthSession(user_id=user_id, token_hash=hash_token(token), expires_at=_now() + ttl))
    logger.info("session_created", user_id=str(user_id))
    return token


async def resolve_session(session: AsyncSession, token: str) -> tuple[AuthSession, User] | None:
    """Token → session valide (non expirée, non révoquée) + user ; None sinon."""
    auth_session = await session.scalar(
        select(AuthSession).where(AuthSession.token_hash == hash_token(token))
    )
    if auth_session is None or auth_session.revoked_at is not None:
        return None
    if auth_session.expires_at < _now():
        return None
    user = await session.get(User, auth_session.user_id)
    if user is None:
        return None
    auth_session.last_seen_at = _now()
    return auth_session, user


async def revoke_session(session: AsyncSession, auth_session: AuthSession) -> None:
    auth_session.revoked_at = _now()
    logger.info("session_revoked", user_id=str(auth_session.user_id))


async def revoke_all_sessions(session: AsyncSession, user_id: uuid.UUID) -> None:
    sessions = await session.scalars(
        select(AuthSession).where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
    )
    now = _now()
    for auth_session in sessions:
        auth_session.revoked_at = now
    logger.info("sessions_revoked_all", user_id=str(user_id))


# --- Login partiel TOTP (jeton court, même mécanique hachée) ---


async def create_login_challenge(session: AsyncSession, user_id: uuid.UUID) -> str:
    token = generate_token()
    ttl = timedelta(minutes=get_settings().login_challenge_ttl_minutes)
    session.add(
        LoginChallenge(user_id=user_id, token_hash=hash_token(token), expires_at=_now() + ttl)
    )
    return token


async def consume_login_challenge(session: AsyncSession, token: str) -> uuid.UUID | None:
    """Consomme le jeton de login partiel (usage unique) ; None si invalide/expiré."""
    challenge = await session.scalar(
        select(LoginChallenge).where(LoginChallenge.token_hash == hash_token(token))
    )
    if challenge is None or challenge.consumed_at is not None or challenge.expires_at < _now():
        return None
    challenge.consumed_at = _now()
    return challenge.user_id


# --- TOTP (pyotp) + codes de récupération ---


def _decrypt_totp_secret(credentials: UserCredentials) -> str:
    if credentials.totp_secret_encrypted is None:
        msg = "Aucun secret TOTP enregistré."
        raise AuthError(msg)
    return get_key_provider().decrypt(credentials.totp_secret_encrypted).decode()


async def setup_totp(session: AsyncSession, user: User) -> tuple[str, str]:
    """Provisionne un secret TOTP (chiffré) ; retourne (secret, URI otpauth).

    Le TOTP n'est actif qu'après `activate_totp` (vérification d'un premier code).
    """
    credentials = await session.get(UserCredentials, user.id)
    if credentials is None:
        credentials = UserCredentials(user_id=user.id)
        session.add(credentials)
    if credentials.totp_enabled:
        msg = "Le TOTP est déjà activé."
        raise AuthError(msg)
    secret = pyotp.random_base32()
    credentials.totp_secret_encrypted = get_key_provider().encrypt(secret.encode())
    credentials.totp_last_counter = None
    uri = pyotp.totp.TOTP(secret).provisioning_uri(  # pyright: ignore[reportUnknownMemberType]
        name=user.email, issuer_name=TOTP_ISSUER
    )
    return secret, uri


def _verify_totp_code(credentials: UserCredentials, code: str) -> bool:
    """Vérifie un code TOTP (fenêtre ±1) avec anti-rejeu par compteur."""
    secret = _decrypt_totp_secret(credentials)
    totp = pyotp.TOTP(secret)
    now = _now()
    timestamp = int(now.timestamp())
    for offset in (0, -1, 1):
        counter = timestamp // totp.interval + offset
        if not secrets.compare_digest(totp.at(counter * totp.interval), code):
            continue
        if credentials.totp_last_counter is not None and counter <= credentials.totp_last_counter:
            return False  # rejeu : code déjà consommé
        credentials.totp_last_counter = counter
        return True
    return False


async def activate_totp(session: AsyncSession, user_id: uuid.UUID, code: str) -> list[str]:
    """Active le TOTP après vérification d'un premier code ; retourne les codes
    de récupération — leur seule apparition en clair."""
    credentials = await session.get(UserCredentials, user_id)
    if credentials is None or credentials.totp_secret_encrypted is None:
        msg = "Aucun enrôlement TOTP en cours."
        raise AuthError(msg)
    if credentials.totp_enabled:
        msg = "Le TOTP est déjà activé."
        raise AuthError(msg)
    if not _verify_totp_code(credentials, code):
        msg = "Code TOTP invalide."
        raise AuthError(msg)
    credentials.totp_enabled = True

    codes = [secrets.token_hex(5) for _ in range(RECOVERY_CODE_COUNT)]
    session.add_all(RecoveryCode(user_id=user_id, code_hash=hash_token(code)) for code in codes)
    logger.info("totp_activated", user_id=str(user_id))
    return codes


async def disable_totp(session: AsyncSession, user_id: uuid.UUID, password: str) -> None:
    """Désactive le TOTP — le mot de passe est exigé."""
    credentials = await session.get(UserCredentials, user_id)
    if credentials is None or not credentials.totp_enabled:
        msg = "Le TOTP n'est pas activé."
        raise AuthError(msg)
    if (
        credentials.password_hash is None
        or not verify_password(credentials.password_hash, password)[0]
    ):
        msg = "Mot de passe invalide."
        raise AuthError(msg)
    credentials.totp_enabled = False
    credentials.totp_secret_encrypted = None
    credentials.totp_last_counter = None
    existing = await session.scalars(select(RecoveryCode).where(RecoveryCode.user_id == user_id))
    for recovery_code in existing:
        await session.delete(recovery_code)
    logger.info("totp_disabled", user_id=str(user_id))


async def verify_second_factor(session: AsyncSession, user_id: uuid.UUID, code: str) -> bool:
    """Second facteur au login : code TOTP, ou code de récupération (usage unique)."""
    credentials = await session.get(UserCredentials, user_id)
    if credentials is None or not credentials.totp_enabled:
        return False
    if _verify_totp_code(credentials, code):
        return True
    recovery = await session.scalar(
        select(RecoveryCode).where(
            RecoveryCode.user_id == user_id,
            RecoveryCode.code_hash == hash_token(code),
            RecoveryCode.used_at.is_(None),
        )
    )
    if recovery is None:
        return False
    recovery.used_at = _now()
    logger.info("recovery_code_used", user_id=str(user_id))
    return True
