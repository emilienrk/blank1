"""Routes du module `sample_digest` (Phase 7 T6).

Montées par le runtime sous `/api/v1/modules/sample_digest/…` avec la dépendance
`require_module_enabled("sample_digest")` (403 si le module n'est pas actif). Toutes
les routes portent `require_permission("sample_digest.…")` — l'invariant racine n°9
appliqué aux modules, vérifié au démarrage (D2).
"""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.permissions import require_permission
from app.modules.sample_digest.service import MODULE_NAME, TASK_NAME
from app.modules.sample_digest.tenant_models import SampleDigestDigest
from app.tenancy.context import TenantContext
from app.tenancy.session import get_tenant_session

router = APIRouter(tags=["module:sample_digest"])

TenantSession = Annotated[AsyncSession, Depends(get_tenant_session)]

PERM_READ = "sample_digest.read"
PERM_MANAGE = "sample_digest.manage"


class DigestOut(BaseModel):
    id: uuid.UUID
    generated_at: datetime
    message_count: int
    summary: str


class RunResponse(BaseModel):
    status: Literal["scheduled"] = "scheduled"


@router.get("/digests", operation_id="sampleDigestListDigests")
async def list_digests(
    _: Annotated[TenantContext, Depends(require_permission(PERM_READ))],
    tenant_session: TenantSession,
) -> list[DigestOut]:
    digests = await tenant_session.scalars(
        select(SampleDigestDigest).order_by(SampleDigestDigest.generated_at.desc())
    )
    return [
        DigestOut(
            id=digest.id,
            generated_at=digest.generated_at,
            message_count=digest.message_count,
            summary=digest.summary,
        )
        for digest in digests.all()
    ]


@router.post("/run", operation_id="sampleDigestRun", status_code=202)
async def run_now(
    ctx: Annotated[TenantContext, Depends(require_permission(PERM_MANAGE))],
) -> RunResponse:
    """Déclenchement manuel de la tâche (T6) : dispatch de la même tâche unitaire que
    le scheduler pour le tenant courant — jamais d'appel lourd dans la requête HTTP."""
    # Import paresseux : le scheduler importe le registre qui importe ce module.
    from app.automation import scheduler

    await scheduler.enqueue_unit(MODULE_NAME, TASK_NAME, ctx.tenant_id)
    return RunResponse()
