"""Routes du back-office (Phase 3 T6) : `/api/v1/admin/*`, hors contexte tenant.

Toutes derrière `require_platform_admin` — première exposition de cette
dépendance (invariant de phase n°1). Le vhost public ne sert jamais ces
routes (défense en profondeur réseau, décision D8 — voir `infra/caddy`).
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin import service
from app.admin import tasks as admin_tasks
from app.admin.models import MigrationOutcomeDict, MigrationReportRecord
from app.auth.deps import require_platform_admin
from app.core.db import get_control_session
from app.directory.models import User
from app.directory.service import DirectoryError
from app.tenancy.provisioning import ProvisioningError, provision_tenant, retry_provision

router = APIRouter(prefix="/admin", tags=["admin"])

ControlSession = Annotated[AsyncSession, Depends(get_control_session)]
PlatformAdmin = Annotated[User, Depends(require_platform_admin)]


# --- Tenants ---


class TenantSummaryOut(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    state: str
    plan: str
    db_name: str
    schema_revision: str | None


def _tenant_out(summary: service.TenantSummary) -> TenantSummaryOut:
    return TenantSummaryOut(
        id=summary.id,
        slug=summary.slug,
        name=summary.name,
        state=summary.state,
        plan=summary.plan,
        db_name=summary.db_name,
        schema_revision=summary.schema_revision,
    )


@router.get("/tenants", operation_id="adminListTenants")
async def tenants_list(_: PlatformAdmin, session: ControlSession) -> list[TenantSummaryOut]:
    summaries = await service.list_tenants(session)
    return [_tenant_out(summary) for summary in summaries]


class CreateTenantRequest(BaseModel):
    slug: str
    name: str | None = None
    # Invitation du premier owner en fin de provisioning (mêmes règles que le CLI).
    owner_email: EmailStr | None = None


class CreateTenantResponse(BaseModel):
    tenant: TenantSummaryOut
    # Toujours retournée quand demandée (décision D8 Phase 2, inchangée).
    owner_invitation_accept_url: str | None = None


@router.post("/tenants", operation_id="adminCreateTenant", status_code=201)
async def tenants_create(
    payload: CreateTenantRequest, _: PlatformAdmin, session: ControlSession
) -> CreateTenantResponse:
    try:
        tenant = await provision_tenant(payload.slug, payload.name or payload.slug)
    except (ValueError, ProvisioningError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    accept_url: str | None = None
    if payload.owner_email is not None:
        try:
            accept_url = await service.create_tenant_invitation(
                session, tenant.id, payload.owner_email
            )
        except (ValueError, DirectoryError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    summary = await service.tenant_summary(tenant)
    return CreateTenantResponse(tenant=_tenant_out(summary), owner_invitation_accept_url=accept_url)


@router.post("/tenants/{slug}/retry-provision", operation_id="adminRetryProvisionTenant")
async def tenants_retry_provision(slug: str, _: PlatformAdmin) -> TenantSummaryOut:
    try:
        tenant = await retry_provision(slug)
    except ProvisioningError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    summary = await service.tenant_summary(tenant)
    return _tenant_out(summary)


# --- Utilisateurs (diagnostic support) ---


class MembershipSummaryOut(BaseModel):
    tenant_slug: str
    role: str


class UserLookupOut(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str | None
    is_platform_admin: bool
    memberships: list[MembershipSummaryOut]


@router.get("/users/{email}", operation_id="adminLookupUser")
async def users_lookup(email: str, _: PlatformAdmin, session: ControlSession) -> UserLookupOut:
    result = await service.lookup_user(session, email)
    if result is None:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable")
    return UserLookupOut(
        id=result.user.id,
        email=result.user.email,
        display_name=result.user.display_name,
        is_platform_admin=result.user.is_platform_admin,
        memberships=[
            MembershipSummaryOut(tenant_slug=slug, role=role) for slug, role in result.memberships
        ],
    )


# --- Migrations (décision D6 : Celery + rapport persisté + polling) ---


class MigrationReportOut(BaseModel):
    id: uuid.UUID
    status: str
    summary: str | None
    error: str | None
    outcomes: list[MigrationOutcomeDict]
    started_at: str
    finished_at: str | None


def _report_out(record: MigrationReportRecord) -> MigrationReportOut:
    return MigrationReportOut(
        id=record.id,
        status=str(record.status),
        summary=record.summary,
        error=record.error,
        outcomes=record.outcomes,
        started_at=record.started_at.isoformat(),
        finished_at=record.finished_at.isoformat() if record.finished_at is not None else None,
    )


@router.post("/migrations/run", operation_id="adminRunMigrations", status_code=202)
async def migrations_run(_: PlatformAdmin, session: ControlSession) -> MigrationReportOut:
    record = await service.start_migration_report(session)
    await session.commit()
    await admin_tasks.enqueue_migration_run(record.id)
    return _report_out(record)


@router.get("/migrations/last-report", operation_id="adminGetLastMigrationReport")
async def migrations_last_report(
    _: PlatformAdmin, session: ControlSession
) -> MigrationReportOut | None:
    record = await service.get_last_report(session)
    return _report_out(record) if record is not None else None
