"""Routes du back-office (Phase 3 T6) : `/api/v1/admin/*`, hors contexte tenant.

Toutes derrière `require_platform_admin` — première exposition de cette
dépendance (invariant de phase n°1). Le vhost public ne sert jamais ces
routes (défense en profondeur réseau, décision D8 — voir `infra/caddy`).
"""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, EmailStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin import service
from app.admin import tasks as admin_tasks
from app.admin.models import MigrationOutcomeDict, MigrationReportRecord
from app.ai import admin_service as ai_admin
from app.auth.deps import require_platform_admin
from app.core.db import get_control_session
from app.directory.models import User
from app.directory.service import DirectoryError
from app.gdpr import tasks as gdpr_tasks
from app.gdpr.erasure import GdprErasureError, cancel_erasure, request_erasure
from app.gdpr.export import GdprExportError, export_path, list_exports
from app.tenancy.provisioning import ProvisioningError, provision_tenant, retry_provision

router = APIRouter(prefix="/admin", tags=["admin"])

ControlSession = Annotated[AsyncSession, Depends(get_control_session)]
PlatformAdmin = Annotated[User, Depends(require_platform_admin)]


class StatusResponse(BaseModel):
    status: Literal["ok"] = "ok"


# --- Tenants ---


class TenantSummaryOut(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    state: str
    plan: str
    db_name: str
    schema_revision: str | None
    deletion_requested_at: datetime | None
    erasure_due_at: datetime | None


def _tenant_out(summary: service.TenantSummary) -> TenantSummaryOut:
    return TenantSummaryOut(
        id=summary.id,
        slug=summary.slug,
        name=summary.name,
        state=summary.state,
        plan=summary.plan,
        db_name=summary.db_name,
        schema_revision=summary.schema_revision,
        deletion_requested_at=summary.deletion_requested_at,
        erasure_due_at=summary.erasure_due_at,
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


# --- RGPD : export (T4) ---


class ExportFileOut(BaseModel):
    filename: str
    size_bytes: int
    created_at: datetime


@router.post("/tenants/{slug}/export", operation_id="adminExportTenant", status_code=202)
async def tenants_export(slug: str, _: PlatformAdmin) -> StatusResponse:
    """Dispatch Celery (T4) : `pg_dump` peut durer, la route ne bloque jamais dessus —
    consulter `GET .../exports` pour l'archive une fois prête."""
    await gdpr_tasks.enqueue_export(slug)
    return StatusResponse()


@router.get("/tenants/{slug}/exports", operation_id="adminListTenantExports")
async def tenants_exports_list(slug: str, _: PlatformAdmin) -> list[ExportFileOut]:
    return [
        ExportFileOut(filename=f.filename, size_bytes=f.size_bytes, created_at=f.created_at)
        for f in list_exports(slug)
    ]


@router.get("/tenants/{slug}/exports/{filename}/download", operation_id="adminDownloadTenantExport")
async def tenants_export_download(slug: str, filename: str, _: PlatformAdmin) -> FileResponse:
    try:
        path = export_path(slug, filename)
    except GdprExportError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(path, media_type="application/octet-stream", filename=filename)


# --- RGPD : effacement (T5) ---


@router.post("/tenants/{slug}/request-erasure", operation_id="adminRequestTenantErasure")
async def tenants_request_erasure(slug: str, _: PlatformAdmin) -> TenantSummaryOut:
    try:
        tenant = await request_erasure(slug)
    except GdprErasureError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    summary = await service.tenant_summary(tenant)
    return _tenant_out(summary)


@router.post("/tenants/{slug}/cancel-erasure", operation_id="adminCancelTenantErasure")
async def tenants_cancel_erasure(slug: str, _: PlatformAdmin) -> TenantSummaryOut:
    try:
        tenant = await cancel_erasure(slug)
    except GdprErasureError as exc:
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


# --- Gateway IA (Phase 6 T6) : consommation + politiques par tenant ---


class AIUsageOut(BaseModel):
    tenant_id: uuid.UUID
    slug: str
    name: str
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    request_count: int
    error_count: int
    estimated_cost_microeur: int
    total_tokens: int
    monthly_token_quota: int
    over_quota: bool


@router.get("/ai/usage", operation_id="adminAIUsage")
async def ai_usage(
    _: PlatformAdmin, session: ControlSession, month: str | None = None
) -> list[AIUsageOut]:
    """Agrégats d'usage IA par tenant pour un mois (défaut : mois courant), avec les
    dépassements de quota (`over_quota`)."""
    usages = await ai_admin.list_usage(session, month)
    return [
        AIUsageOut(
            tenant_id=u.tenant_id,
            slug=u.slug,
            name=u.name,
            input_tokens=u.input_tokens,
            output_tokens=u.output_tokens,
            cached_tokens=u.cached_tokens,
            request_count=u.request_count,
            error_count=u.error_count,
            estimated_cost_microeur=u.estimated_cost_microeur,
            total_tokens=u.total_tokens,
            monthly_token_quota=u.monthly_token_quota,
            over_quota=u.over_quota,
        )
        for u in usages
    ]


class AIPolicyOut(BaseModel):
    slug: str
    default_provider: str | None
    default_model: str | None
    allowed_providers: list[str]
    zero_retention: bool
    monthly_token_quota: int | None
    hard_limit_enabled: bool
    fallback_provider: str | None
    fallback_model: str | None
    byok_configured: bool


def _policy_out(view: ai_admin.PolicyView) -> AIPolicyOut:
    return AIPolicyOut(
        slug=view.slug,
        default_provider=view.default_provider,
        default_model=view.default_model,
        allowed_providers=view.allowed_providers,
        zero_retention=view.zero_retention,
        monthly_token_quota=view.monthly_token_quota,
        hard_limit_enabled=view.hard_limit_enabled,
        fallback_provider=view.fallback_provider,
        fallback_model=view.fallback_model,
        byok_configured=view.byok_configured,
    )


@router.get("/tenants/{slug}/ai-policy", operation_id="adminGetTenantAIPolicy")
async def ai_policy_get(slug: str, _: PlatformAdmin, session: ControlSession) -> AIPolicyOut:
    try:
        view = await ai_admin.get_policy_view(session, slug)
    except ai_admin.AIPolicyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _policy_out(view)


class AIPolicyIn(BaseModel):
    default_provider: str | None = None
    default_model: str | None = None
    allowed_providers: list[str] = []
    zero_retention: bool = False
    monthly_token_quota: int | None = None
    hard_limit_enabled: bool = False
    fallback_provider: str | None = None
    fallback_model: str | None = None


@router.put("/tenants/{slug}/ai-policy", operation_id="adminSetTenantAIPolicy")
async def ai_policy_set(
    slug: str, payload: AIPolicyIn, admin: PlatformAdmin, session: ControlSession
) -> AIPolicyOut:
    try:
        view = await ai_admin.set_policy(
            session,
            slug,
            ai_admin.PolicyUpdate(
                default_provider=payload.default_provider,
                default_model=payload.default_model,
                allowed_providers=payload.allowed_providers,
                zero_retention=payload.zero_retention,
                monthly_token_quota=payload.monthly_token_quota,
                hard_limit_enabled=payload.hard_limit_enabled,
                fallback_provider=payload.fallback_provider,
                fallback_model=payload.fallback_model,
            ),
            actor_user_id=admin.id,
        )
    except ai_admin.AIPolicyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _policy_out(view)
