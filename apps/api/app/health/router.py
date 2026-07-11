from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core.config import Settings, get_settings

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: Literal["ok"]
    version: str
    env: str


@router.get("/health", operation_id="getHealth")
def get_health(settings: Annotated[Settings, Depends(get_settings)]) -> HealthResponse:
    """Sonde de vie : aucun accès DB en Phase 0."""
    return HealthResponse(status="ok", version=settings.app_version, env=settings.app_env)
