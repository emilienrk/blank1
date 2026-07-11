from fastapi import FastAPI

from app.auth.router import router as auth_router
from app.core.config import get_settings
from app.core.csrf import CsrfOriginMiddleware
from app.core.logging import RequestIdMiddleware, configure_logging
from app.directory.router import router as directory_router
from app.health.router import router as health_router


def create_app() -> FastAPI:
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
    return app


app = create_app()
