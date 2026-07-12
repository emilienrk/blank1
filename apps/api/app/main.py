from fastapi import FastAPI

from app.admin.router import router as admin_router
from app.ai.router import router as ai_router
from app.audit.router import router as audit_router
from app.auth.router import router as auth_router
from app.connectors.oauth import router as connectors_oauth_router
from app.connectors.router import router as connectors_router
from app.connectors.webhooks import router as webhooks_router
from app.core.config import get_settings
from app.core.csrf import CsrfOriginMiddleware
from app.core.logging import RequestIdMiddleware, configure_logging
from app.directory.router import router as directory_router
from app.health.router import router as health_router


def create_app() -> FastAPI:
    # Construit et enregistre l'app Celery configurée (broker Valkey) comme app « courante » :
    # indispensable pour que `run_migrations_task.delay(...)` (T6, décision D6) parte sur le
    # bon broker — sans cet import, `@shared_task` retombe sur l'app Celery par défaut (non
    # configurée) dans CE process API (le process worker l'importe déjà de son côté).
    import app.worker  # pyright: ignore[reportUnusedImport]

    settings = get_settings()
    configure_logging(settings.log_level)
    app = FastAPI(
        title="Socle SaaS API",
        version=settings.app_version,
        openapi_url="/api/v1/openapi.json",
        docs_url="/api/v1/docs",
    )
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(CsrfOriginMiddleware)
    app.include_router(health_router, prefix="/api/v1")
    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(directory_router, prefix="/api/v1")
    app.include_router(audit_router, prefix="/api/v1")
    app.include_router(connectors_router, prefix="/api/v1")
    app.include_router(ai_router, prefix="/api/v1")
    # Routes anonymes des connecteurs (liste fermée, invariant n°9) : callback
    # OAuth tiers + webhooks providers.
    app.include_router(connectors_oauth_router, prefix="/api/v1")
    app.include_router(webhooks_router, prefix="/api/v1")
    app.include_router(admin_router, prefix="/api/v1")
    return app


app = create_app()
