# TestClient (starlette/httpx) expose des membres partiellement typés : on relâche
# uniquement les règles "unknown" sur ce fichier de test.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
import json

import pytest
from fastapi.testclient import TestClient

from app.core.logging import REQUEST_ID_HEADER


def test_request_id_generated_when_absent(client: TestClient) -> None:
    response = client.get("/api/v1/health")

    request_id = response.headers.get(REQUEST_ID_HEADER)
    assert request_id
    assert len(request_id) == 32  # uuid4 hex


def test_request_id_propagated_when_present(client: TestClient) -> None:
    response = client.get("/api/v1/health", headers={REQUEST_ID_HEADER: "abc-123"})

    assert response.headers[REQUEST_ID_HEADER] == "abc-123"


def test_request_id_present_in_json_logs(
    client: TestClient, capsys: pytest.CaptureFixture[str]
) -> None:
    client.get("/api/v1/health", headers={REQUEST_ID_HEADER: "log-correlation-id"})

    access_logs = [
        json.loads(line)
        for line in capsys.readouterr().out.splitlines()
        if '"http_request"' in line
    ]
    assert access_logs, "le middleware doit émettre une ligne de log d'accès JSON"
    log = access_logs[-1]
    assert log["request_id"] == "log-correlation-id"
    assert log["path"] == "/api/v1/health"
    assert log["status"] == 200
