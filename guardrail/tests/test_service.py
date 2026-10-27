import json

import pytest
import structlog
from fastapi.testclient import TestClient

from hackathon_guardrail import api
from hackathon_guardrail.config import get_settings
from hackathon_guardrail.extensions import configure_logging
from hackathon_guardrail.main import app


@pytest.fixture(autouse=True)
def logging_settings(monkeypatch):
    monkeypatch.setenv("LOG_FORMAT", "json")
    get_settings.cache_clear()
    configure_logging()
    yield
    get_settings.cache_clear()


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/request", {"messages": [{"role": "user", "content": "private prompt"}]}),
        (
            "/response",
            {
                "choices": [
                    {"message": {"role": "assistant", "content": "private answer"}}
                ]
            },
        ),
        ("/request", {}),
        ("/response", {}),
    ],
)
def test_passes_and_logs_metadata_without_credentials_or_content(path, body, caplog):
    response = TestClient(app).post(
        path,
        json={"body": body},
        headers={
            "x-user-id": "user-1",
            "x-model": "any-model",
            "x-request-id": "request-1",
            "x-extra": "extra context",
            "authorization": "Bearer private-key",
            "cookie": "session=private-cookie",
            "X-Api-Key": "private-api-key",
            "X-Access-Token": "private-token",
            "X-Client-Secret": "private-secret",
            "X-Password": "private-password",
        },
    )
    assert response.status_code == 200
    assert response.json() == {"action": {"reason": None}}
    [event] = [record.msg for record in caplog.records if record.name == "guardrail"]
    assert event["message"] == "guardrail_checked"
    assert event["phase"] == path[1:]
    assert event["user_id"] == "user-1"
    assert event["model"] == "any-model"
    assert event["request_id"] == event["correlation_id"] == "request-1"
    assert event["message_count"] == (1 if body else 0)
    assert event["content_chars"] == (len("private prompt") if body else 0)
    assert event["headers"]["x-extra"] == "extra context"
    assert event["headers"]["authorization"] == "[REDACTED]"
    assert "private" not in json.dumps(event)
    assert "correlation_id" not in structlog.contextvars.get_contextvars()


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/request", {}),
        ("/request", {"body": {"messages": [{"role": "user"}]}}),
        ("/request", {"body": {"messages": "invalid"}}),
        (
            "/response",
            {"body": {"choices": [{"message": {"content": "missing role"}}]}},
        ),
        ("/response", {"body": {"choices": [None]}}),
    ],
)
def test_rejects_invalid_webhook_payloads(path, body):
    assert TestClient(app).post(path, json=body).status_code == 422


def test_unhandled_errors_use_backend_error_response(monkeypatch):
    def fail(*args):
        raise RuntimeError("webhook failed")

    monkeypatch.setattr(api, "log_webhook", fail)
    response = TestClient(app, raise_server_exceptions=False).post(
        "/request", json={"body": {"messages": []}}
    )
    assert response.status_code == 500
    assert response.json() == {"message": "Unhandled exception occurred"}


def test_startup_health_and_openapi():
    with TestClient(app) as client:
        assert client.get("/api/health").json() == {"status": "ok"}
        paths = client.get("/openapi.json").json()["paths"]
    assert "post" in paths["/request"]
    assert "post" in paths["/response"]
