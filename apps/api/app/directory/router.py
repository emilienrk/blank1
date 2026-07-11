"""Routes annuaire (tenant courant) : membres, invitations, équipes (Phase 2 T8).

Toutes ces routes sont sous contexte tenant (`require_permission` compose
`resolve_tenant` + session + membership). Les équipes vivent en DB TENANT :
leurs handlers sont la preuve vivante de la pile Phase 1 en HTTP réel
(resolve_tenant → get_tenant_session).
"""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import CurrentAuth, current_auth
from app.auth.permissions import require_permission
from app.core.db import get_control_session
from app.core.mailer import get_mailer
from app.directory import service
from app.directory.models import User
from app.directory.tenant_models import Team, TeamMember
from app.tenancy.context import TenantContext
from app.tenancy.session import get_tenant_session

router = APIRouter(prefix="/directory", tags=["directory"])

ControlSession = Annotated[AsyncSession, Depends(get_control_session)]
TenantSession = Annotated[AsyncSession, Depends(get_tenant_session)]


class StatusResponse(BaseModel):
    status: Literal["ok"] = "ok"


# --- Membres ---


class MemberOut(BaseModel):
    user_id: uuid.UUID
    email: str
    display_name: str | None
    role: str


@router.get("/members", operation_id="listMembers")
async def members_list(
    ctx: Annotated[TenantContext, Depends(require_permission("core.members.read"))],
    session: ControlSession,
) -> list[MemberOut]:
    members = await service.list_members(session, ctx.tenant_id)
    return [
        MemberOut(
            user_id=user.id, email=user.email, display_name=user.display_name, role=member.role
        )
        for user, member in members
    ]


class ChangeRoleRequest(BaseModel):
    role: str


@router.patch("/members/{user_id}", operation_id="changeMemberRole")
async def member_change_role(
    user_id: uuid.UUID,
    payload: ChangeRoleRequest,
    ctx: Annotated[TenantContext, Depends(require_permission("core.members.manage"))],
    session: ControlSession,
) -> MemberOut:
    assert ctx.role is not None  # garanti par resolve_tenant
    try:
        membership = await service.change_member_role(
            session, ctx.tenant_id, user_id, payload.role, actor_role=ctx.role
        )
    except (ValueError, service.DirectoryError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await session.commit()
    user = await session.get_one(User, user_id)
    return MemberOut(
        user_id=user.id, email=user.email, display_name=user.display_name, role=membership.role
    )


@router.delete("/members/{user_id}", operation_id="removeMember")
async def member_remove(
    user_id: uuid.UUID,
    ctx: Annotated[TenantContext, Depends(require_permission("core.members.manage"))],
    session: ControlSession,
) -> StatusResponse:
    assert ctx.role is not None
    try:
        await service.remove_member(session, ctx.tenant_id, user_id, actor_role=ctx.role)
    except service.DirectoryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await session.commit()
    return StatusResponse()


# --- Invitations ---


class CreateInvitationRequest(BaseModel):
    email: EmailStr
    role: str


class InvitationOut(BaseModel):
    id: uuid.UUID
    role: str
    expires_at: datetime
    # L'URL d'acceptation est TOUJOURS retournée à l'appelant autorisé (décision D8) ;
    # l'envoi d'email est optionnel (SMTP configuré ou non).
    accept_url: str


@router.post("/invitations", operation_id="createInvitation", status_code=201)
async def invitation_create(
    payload: CreateInvitationRequest,
    ctx: Annotated[TenantContext, Depends(require_permission("core.members.manage"))],
    auth: Annotated[CurrentAuth, Depends(current_auth)],
    session: ControlSession,
) -> InvitationOut:
    try:
        invitation, token = await service.create_invitation(
            session,
            ctx.tenant_id,
            payload.email,
            payload.role,
            actor_role=ctx.role,
            invited_by_user_id=auth.user.id,
        )
    except (ValueError, service.DirectoryError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await session.commit()
    accept_url = service.accept_url_for(token)
    await get_mailer().send(
        payload.email,
        "Invitation à rejoindre un espace de travail",
        f"Vous êtes invité(e) à rejoindre l'espace {ctx.slug}.\n"
        f"Acceptez l'invitation ici : {accept_url}\n",
    )
    return InvitationOut(
        id=invitation.id,
        role=invitation.role,
        expires_at=invitation.expires_at,
        accept_url=accept_url,
    )


@router.delete("/invitations/{invitation_id}", operation_id="revokeInvitation")
async def invitation_revoke(
    invitation_id: uuid.UUID,
    ctx: Annotated[TenantContext, Depends(require_permission("core.members.manage"))],
    session: ControlSession,
) -> StatusResponse:
    try:
        await service.revoke_invitation(session, ctx.tenant_id, invitation_id)
    except service.DirectoryError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await session.commit()
    return StatusResponse()


# --- Équipes (DB tenant) ---


class TeamOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None


class CreateTeamRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)


@router.get("/teams", operation_id="listTeams")
async def teams_list(
    ctx: Annotated[TenantContext, Depends(require_permission("core.teams.read"))],
    session: TenantSession,
) -> list[TeamOut]:
    teams = await session.scalars(select(Team).order_by(Team.name))
    return [TeamOut(id=team.id, name=team.name, description=team.description) for team in teams]


@router.post("/teams", operation_id="createTeam", status_code=201)
async def team_create(
    payload: CreateTeamRequest,
    ctx: Annotated[TenantContext, Depends(require_permission("core.teams.manage"))],
    session: TenantSession,
) -> TeamOut:
    existing = await session.scalar(select(Team).where(Team.name == payload.name))
    if existing is not None:
        raise HTTPException(status_code=409, detail="Une équipe porte déjà ce nom")
    team = Team(name=payload.name, description=payload.description)
    session.add(team)
    await session.commit()
    return TeamOut(id=team.id, name=team.name, description=team.description)


@router.delete("/teams/{team_id}", operation_id="deleteTeam")
async def team_delete(
    team_id: uuid.UUID,
    ctx: Annotated[TenantContext, Depends(require_permission("core.teams.manage"))],
    session: TenantSession,
) -> StatusResponse:
    team = await session.get(Team, team_id)
    if team is None:
        raise HTTPException(status_code=404, detail="Équipe introuvable")
    await session.delete(team)
    await session.commit()
    return StatusResponse()


class AddTeamMemberRequest(BaseModel):
    user_id: uuid.UUID


@router.post("/teams/{team_id}/members", operation_id="addTeamMember", status_code=201)
async def team_member_add(
    team_id: uuid.UUID,
    payload: AddTeamMemberRequest,
    ctx: Annotated[TenantContext, Depends(require_permission("core.teams.manage"))],
    control_session: ControlSession,
    session: TenantSession,
) -> StatusResponse:
    team = await session.get(Team, team_id)
    if team is None:
        raise HTTPException(status_code=404, detail="Équipe introuvable")
    # Cohérence inter-bases : le user doit être membre du tenant (pas de FK possible).
    membership = await service.get_membership(control_session, ctx.tenant_id, payload.user_id)
    if membership is None:
        raise HTTPException(status_code=400, detail="Cet utilisateur n'est pas membre du tenant")
    duplicate = await session.scalar(
        select(TeamMember).where(
            TeamMember.team_id == team_id, TeamMember.user_id == payload.user_id
        )
    )
    if duplicate is not None:
        raise HTTPException(status_code=409, detail="Déjà membre de l'équipe")
    session.add(TeamMember(team_id=team_id, user_id=payload.user_id))
    await session.commit()
    return StatusResponse()


@router.delete("/teams/{team_id}/members/{user_id}", operation_id="removeTeamMember")
async def team_member_remove(
    team_id: uuid.UUID,
    user_id: uuid.UUID,
    ctx: Annotated[TenantContext, Depends(require_permission("core.teams.manage"))],
    session: TenantSession,
) -> StatusResponse:
    team_member = await session.scalar(
        select(TeamMember).where(TeamMember.team_id == team_id, TeamMember.user_id == user_id)
    )
    if team_member is None:
        raise HTTPException(status_code=404, detail="Membre d'équipe introuvable")
    await session.delete(team_member)
    await session.commit()
    return StatusResponse()
