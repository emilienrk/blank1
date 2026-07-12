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
    beat_schedule={
        # Purge des sessions/challenges/invitations expirés (Phase 2 T9).
        "auth-purge-expired": {"task": "core.auth.purge_expired", "schedule": 3600.0},
        # Rétention/purge par tenant (Phase 4 T6) — quotidien, décision D7.
        "gdpr-apply-retention-policies": {
            "task": "core.gdpr.apply_retention_policies",
            "schedule": 86400.0,
        },
        # Effacements dont le délai de grâce est écoulé (Phase 4 T5, décision D2) —
        # horaire : le délai de grâce se compte en jours, une heure de latence est sans
        # conséquence et raccourcit le rattrapage après un échec partiel.
        "gdpr-execute-pending-erasures": {
            "task": "core.gdpr.execute_pending_erasures",
            "schedule": 3600.0,
        },
        # Purge des archives d'export au-delà du TTL (Phase 4 T4).
        "gdpr-purge-expired-exports": {
            "task": "core.gdpr.purge_expired_exports",
            "schedule": 3600.0,
        },
        # Refresh proactif des tokens de connecteurs (Phase 5 T6, décision D5).
        "connectors-refresh-tokens": {
            "task": "connectors.refresh_expiring_tokens",
            "schedule": 300.0,
        },
        # Renouvellement des subscriptions webhook providers (Phase 5 T8).
        "connectors-renew-subscriptions": {
            "task": "connectors.renew_subscriptions",
            "schedule": 3600.0,
        },
    },
)

# Les tâches déclarées par module (@shared_task) se rattachent à l'app par import.
import app.admin.tasks  # noqa: E402  # pyright: ignore[reportUnusedImport]
import app.auth.tasks  # noqa: E402  # pyright: ignore[reportUnusedImport]
import app.connectors.tasks  # noqa: E402  # pyright: ignore[reportUnusedImport]
import app.gdpr.tasks  # noqa: E402, F401  # pyright: ignore[reportUnusedImport]


@celery_app.task(name="core.ping")
def ping() -> str:
    """Tâche de démonstration : prouve que le worker consomme bien le broker."""
    return "pong"
