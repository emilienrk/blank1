"""Tâche Celery du back-office (Phase 3 T6, décision D6).

Le runner de migrations peut itérer sur de nombreuses bases : au-delà du
timeout HTTP c'est intenable en synchrone (mêmes raisons que l'entretien
d'auth, `app.auth.tasks`). Le rapport est persisté (`migration_reports`),
la route HTTP ne fait que déclencher puis rendre la main (polling ensuite).
"""

# Celery n'expose pas de types (voir app/worker.py) ; `.delay` sur une tâche
# décorée n'est pas résolu par les stubs partiels (reportCallIssue).
# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUntypedFunctionDecorator=false
# pyright: reportUnknownVariableType=false, reportCallIssue=false

import asyncio
import uuid

from celery import shared_task

from app.admin.service import execute_migration_report
from app.core.db import dispose_control_engine
from app.tenancy.engine_manager import dispose_engine_manager


async def _run(report_id: str) -> None:
    try:
        await execute_migration_report(uuid.UUID(report_id))
    finally:
        # Pools asyncpg liés à leur event loop (cf. app/cli.py, app/auth/tasks.py).
        await dispose_control_engine()
        await dispose_engine_manager()


@shared_task(name="core.admin.run_migrations")
def run_migrations_task(report_id: str) -> None:
    asyncio.run(_run(report_id))


async def enqueue_migration_run(report_id: uuid.UUID) -> None:
    """Frontière de dispatch vers Celery — remplacée en test (pas de broker requis)."""
    run_migrations_task.delay(str(report_id))
