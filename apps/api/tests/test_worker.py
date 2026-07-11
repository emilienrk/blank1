# Celery n'est pas typé : le décorateur masque les attributs de tâche (.apply).
# pyright: reportUnknownMemberType=false, reportFunctionMemberAccess=false, reportUnknownVariableType=false
from app.worker import ping


def test_ping_task_runs_eagerly() -> None:
    result = ping.apply()

    assert result.successful()
    assert result.get() == "pong"
