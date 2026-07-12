"""Consultation de l'audit (Phase 4 T3) : lecture seule, aucune route d'écriture
ou de suppression — l'émission passe exclusivement par `record_audit_event`.

Pagination par curseur composite `(occurred_at, id)` (décision D4) : stable même
quand des lignes s'insèrent pendant la consultation, contrairement à `OFFSET`.
"""

import base64
import binascii
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.tenant_models import AuditEvent
from app.auth.permissions import require_permission
from app.tenancy.context import TenantContext
from app.tenancy.session import get_tenant_session

router = APIRouter(prefix="/audit", tags=["audit"])

TenantSession = Annotated[AsyncSession, Depends(get_tenant_session)]

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


class AuditEventOut(BaseModel):
    id: uuid.UUID
    occurred_at: datetime
    actor_user_id: uuid.UUID | None
    actor_label: str
    action: str
    resource_type: str
    resource_id: str
    payload: dict[str, object]


class AuditEventPage(BaseModel):
    items: list[AuditEventOut]
    next_cursor: str | None


def _encode_cursor(occurred_at: datetime, event_id: uuid.UUID) -> str:
    raw = f"{occurred_at.isoformat()}|{event_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        occurred_at_raw, id_raw = raw.rsplit("|", 1)
        return datetime.fromisoformat(occurred_at_raw), uuid.UUID(id_raw)
    except (ValueError, binascii.Error) as exc:
        raise HTTPException(status_code=400, detail="Curseur de pagination invalide") from exc


@router.get("/events", operation_id="listAuditEvents")
async def audit_events_list(
    ctx: Annotated[TenantContext, Depends(require_permission("core.audit.read"))],
    session: TenantSession,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    cursor: str | None = None,
    action: str | None = None,
    actor_user_id: uuid.UUID | None = None,
    occurred_from: datetime | None = None,
    occurred_to: datetime | None = None,
) -> AuditEventPage:
    query = select(AuditEvent).order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc())

    if cursor is not None:
        cursor_occurred_at, cursor_id = _decode_cursor(cursor)
        query = query.where(
            or_(
                AuditEvent.occurred_at < cursor_occurred_at,
                and_(
                    AuditEvent.occurred_at == cursor_occurred_at,
                    AuditEvent.id < cursor_id,
                ),
            )
        )
    if action is not None:
        query = query.where(AuditEvent.action == action)
    if actor_user_id is not None:
        query = query.where(AuditEvent.actor_user_id == actor_user_id)
    if occurred_from is not None:
        query = query.where(AuditEvent.occurred_at >= occurred_from)
    if occurred_to is not None:
        query = query.where(AuditEvent.occurred_at <= occurred_to)

    rows = list((await session.scalars(query.limit(limit + 1))).all())
    has_more = len(rows) > limit
    rows = rows[:limit]

    next_cursor = _encode_cursor(rows[-1].occurred_at, rows[-1].id) if has_more and rows else None
    return AuditEventPage(
        items=[
            AuditEventOut(
                id=row.id,
                occurred_at=row.occurred_at,
                actor_user_id=row.actor_user_id,
                actor_label=row.actor_label,
                action=row.action,
                resource_type=row.resource_type,
                resource_id=row.resource_id,
                payload=row.payload,
            )
            for row in rows
        ],
        next_cursor=next_cursor,
    )
