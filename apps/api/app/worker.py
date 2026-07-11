# Celery n'expose pas de types : on relâche les règles strict impossibles à satisfaire
# sur ce seul fichier, sans affaiblir le reste du codebase.
# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUntypedFunctionDecorator=false
from celery import Celery

from app.core.config import get_settings
from app.core.logging import configure_logging

settings = get_settings()
configure_logging(settings.log_level)

celery_app = Celery("worker", broker=settings.valkey_url, backend=settings.valkey_url)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
)


@celery_app.task(name="core.ping")
def ping() -> str:
    """Tâche de démonstration : prouve que le worker consomme bien le broker."""
    return "pong"
