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
        # Agrégation quotidienne de l'usage IA + recalage quotas + purge des bruts
        # (Phase 6 T4). Quotidien : les agrégats se raisonnent à la journée.
        "ai-daily-usage-rollup": {
            "task": "core.ai.daily_usage_rollup",
            "schedule": 86400.0,
        },
    },
)

# Les tâches déclarées par module (@shared_task) se rattachent à l'app par import.
import app.ai.tasks  # noqa: E402  # pyright: ignore[reportUnusedImport]
import app.auth.tasks  # noqa: E402  # pyright: ignore[reportUnusedImport]
import app.automation.scheduler  # noqa: E402  # pyright: ignore[reportUnusedImport]
import app.connectors.tasks  # noqa: E402, F401  # pyright: ignore[reportUnusedImport]

# Runtime d'automatisation (Phase 7) : rattache permissions/actions/handlers des
# modules côté worker (les tâches périodiques auditent `<module>.…`, les handlers
# connecteurs s'exécutent ici) et génère les entrées beat statiques du fan-out (D4).
from app.automation.mounting import register_runtime  # noqa: E402
from app.automation.scheduler import beat_entries  # noqa: E402

register_runtime()
celery_app.conf.beat_schedule.update(beat_entries())


@celery_app.task(name="core.ping")
def ping() -> str:
    """Tâche de démonstration : prouve que le worker consomme bien le broker."""
    return "pong"
