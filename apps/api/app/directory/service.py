"""Annuaire : invitations, membres, règles de rôle (Phase 2 T8).

Règles métier au-delà du RBAC : seul un owner promeut/rétrograde un owner ou
invite un owner ; le dernier owner d'un tenant est intouchable (ni rétrogradé
ni retiré). L'invitation est LA seule porte d'entrée (invariant n°5) : token
opaque haché, TTL, usage unique.
"""

import uuid
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import Invitation
from app.auth.permissions import ROLE_OWNER, validate_role
from app.auth.service import get_user_by_email, set_password
from app.auth.tokens import generate_token, hash_token
from app.core.config import get_settings
from app.directory.models import Membership, User

logger = structlog.get_logger()


class DirectoryError(RuntimeError):
    """Opération d'annuaire refusée (règle métier)."""


def _now() -> datetime:
    return datetime.now(UTC)


def accept_url_for(token: str) -> str:
    """URL d'acceptation — la page SPA correspondante arrive en Phase 3."""
    return f"{get_settings().public_base_url}/accept-invitation?token={token}"


async def _count_owners(session: AsyncSession, tenant_id: uuid.UUID) -> int:
    return (
        await session.scalar(
            select(func.count())
            .select_from(Membership)
            .where(Membership.tenant_id == tenant_id, Membership.role == ROLE_OWNER)
        )
    ) or 0


async def create_invitation(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    email: str,
    role: str,
    *,
    actor_role: str | None = None,
    invited_by_user_id: uuid.UUID | None = None,
) -> tuple[Invitation, str]:
    """Crée une invitation ; retourne (invitation, token en clair — seule apparition).

    `actor_role=None` = contexte admin (CLI/provisioning), non soumis aux règles
    de rôle ; en HTTP, inviter un owner exige d'être owner.
    """
    validate_role(role)
    if role == ROLE_OWNER and actor_role is not None and actor_role != ROLE_OWNER:
        msg = "Seul un owner peut inviter un owner."
        raise DirectoryError(msg)

    existing_user = await get_user_by_email(session, email)
    if existing_user is not None:
        membership = await session.scalar(
            select(Membership).where(
                Membership.user_id == existing_user.id, Membership.tenant_id == tenant_id
            )
        )
        if membership is not None:
            msg = "Cet email est déjà membre du tenant."
            raise DirectoryError(msg)

    pending = await session.scalar(
        select(Invitation).where(
            func.lower(Invitation.email) == email.lower(),
            Invitation.tenant_id == tenant_id,
            Invitation.accepted_at.is_(None),
            Invitation.expires_at > _now(),
        )
    )
    if pending is not None:
        msg = "Une invitation est déjà en cours pour cet email — révoquez-la d'abord."
        raise DirectoryError(msg)

    token = generate_token()
    invitation = Invitation(
        email=email,
        tenant_id=tenant_id,
        role=role,
        token_hash=hash_token(token),
        expires_at=_now() + timedelta(hours=get_settings().invitation_ttl_hours),
        invited_by_user_id=invited_by_user_id,
    )
    session.add(invitation)
    await session.flush()
    logger.info("invitation_created", tenant_id=str(tenant_id), role=role)
    return invitation, token


async def list_pending_invitations(session: AsyncSession, tenant_id: uuid.UUID) -> list[Invitation]:
    """Invitations non acceptées (y compris expirées, pour nettoyage) — jamais le
    token en clair : `accept_url_for` n'apparaît qu'à la création (invariant n°5)."""
    rows = await session.scalars(
        select(Invitation)
        .where(Invitation.tenant_id == tenant_id, Invitation.accepted_at.is_(None))
        .order_by(Invitation.created_at.desc())
    )
    return list(rows.all())


async def revoke_invitation(
    session: AsyncSession, tenant_id: uuid.UUID, invitation_id: uuid.UUID
) -> None:
    invitation = await session.get(Invitation, invitation_id)
    if invitation is None or invitation.tenant_id != tenant_id:
        msg = "Invitation introuvable."
        raise DirectoryError(msg)
    if invitation.accepted_at is not None:
        msg = "Invitation déjà acceptée — retirez plutôt le membre."
        raise DirectoryError(msg)
    await session.delete(invitation)
    logger.info("invitation_revoked", tenant_id=str(tenant_id))


async def accept_invitation(
    session: AsyncSession,
    token: str,
    *,
    password: str | None = None,
    display_name: str | None = None,
) -> User:
    """Consomme une invitation : crée le user au besoin, crée le membership.

    Nouveau user sans mot de passe = compte OAuth-only (il se connectera via
    Google/Microsoft — l'email invité fait foi, décision D5).
    """
    invitation = await session.scalar(
        select(Invitation).where(Invitation.token_hash == hash_token(token))
    )
    if invitation is None or invitation.accepted_at is not None or invitation.expires_at < _now():
        msg = "Invitation invalide ou expirée."
        raise DirectoryError(msg)

    user = await get_user_by_email(session, invitation.email)
    if user is None:
        user = User(email=invitation.email, display_name=display_name)
        session.add(user)
        await session.flush()
        if password is not None:
            await set_password(session, user.id, password)
    else:
        if display_name is not None and user.display_name is None:
            user.display_name = display_name
        if password is not None:
            msg = "Ce compte existe déjà — connectez-vous avec ses identifiants."
            raise DirectoryError(msg)

    membership = await session.scalar(
        select(Membership).where(
            Membership.user_id == user.id, Membership.tenant_id == invitation.tenant_id
        )
    )
    if membership is not None:  # pragma: no cover — create_invitation l'empêche en amont
        msg = "Déjà membre de ce tenant."
        raise DirectoryError(msg)

    session.add(Membership(user_id=user.id, tenant_id=invitation.tenant_id, role=invitation.role))
    invitation.accepted_at = _now()
    logger.info(
        "invitation_accepted",
        tenant_id=str(invitation.tenant_id),
        user_id=str(user.id),
        role=invitation.role,
    )
    return user


async def list_members(
    session: AsyncSession, tenant_id: uuid.UUID
) -> list[tuple[User, Membership]]:
    rows = await session.execute(
        select(User, Membership)
        .join(Membership, Membership.user_id == User.id)
        .where(Membership.tenant_id == tenant_id)
        .order_by(func.lower(User.email))
    )
    return [(row[0], row[1]) for row in rows.all()]


async def get_membership(
    session: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID
) -> Membership | None:
    return await session.scalar(
        select(Membership).where(Membership.user_id == user_id, Membership.tenant_id == tenant_id)
    )


async def change_member_role(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    new_role: str,
    *,
    actor_role: str,
) -> Membership:
    validate_role(new_role)
    membership = await get_membership(session, tenant_id, user_id)
    if membership is None:
        msg = "Membre introuvable."
        raise DirectoryError(msg)
    if ROLE_OWNER in (new_role, membership.role) and actor_role != ROLE_OWNER:
        msg = "Seul un owner peut promouvoir ou rétrograder un owner."
        raise DirectoryError(msg)
    if (
        membership.role == ROLE_OWNER
        and new_role != ROLE_OWNER
        and await _count_owners(session, tenant_id) <= 1
    ):
        msg = "Impossible de rétrograder le dernier owner du tenant."
        raise DirectoryError(msg)
    membership.role = new_role
    logger.info("member_role_changed", tenant_id=str(tenant_id), user_id=str(user_id))
    return membership


async def remove_member(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    actor_role: str,
) -> None:
    membership = await get_membership(session, tenant_id, user_id)
    if membership is None:
        msg = "Membre introuvable."
        raise DirectoryError(msg)
    if membership.role == ROLE_OWNER:
        if actor_role != ROLE_OWNER:
            msg = "Seul un owner peut retirer un owner."
            raise DirectoryError(msg)
        if await _count_owners(session, tenant_id) <= 1:
            msg = "Impossible de retirer le dernier owner du tenant."
            raise DirectoryError(msg)
    await session.delete(membership)
    logger.info("member_removed", tenant_id=str(tenant_id), user_id=str(user_id))
