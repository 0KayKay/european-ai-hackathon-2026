from datetime import datetime
from importlib import import_module
from pathlib import Path
from unittest.mock import Mock, call

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from openinference.instrumentation import get_attributes_from_context
from pydantic_ai.models.openai import OpenAIChatModel
from starlette.requests import Request
from starlette.responses import StreamingResponse

from hackathon.agent.builder import build_agent
from hackathon.api.auth import require_user
from hackathon.config import Settings, get_settings


def test_example_environment_matches_local_database() -> None:
    example = Path(__file__).parents[1] / ".env.example"
    settings = Settings(_env_file=example)

    assert settings.AGENTGATEWAY_URL == "http://localhost:4000/v1"
    assert settings.database_url() == "postgresql://app:devpassword@localhost:5432/app"


def test_health_and_invalid_agent_request(monkeypatch) -> None:
    for key, value in {
        "ENV": "prod",
        "AGENTGATEWAY_URL": "http://localhost:4000/v1",
        "AGENTGATEWAY_API_KEY": "test-gateway-key",
        "GOOGLE_STORAGE_BUCKET": "",
        "PRETTY_DOMAIN": "localhost:5000",
        "DB_USER": "app",
        "DB_PASSWORD": "devpassword",
        "DB_NAME": "app",
    }.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()

    from hackathon.main import app

    with TestClient(app) as client:
        assert client.get("/api/health").json() == {"status": "ok"}
        assert client.post("/api/v1/ag-ui", json={}).status_code == 401
        app.dependency_overrides[require_user] = lambda: "github-user"
        try:
            response = client.post("/api/v1/ag-ui", json={})
            invalid_difficulty = client.post(
                "/api/v1/ag-ui",
                json={
                    "threadId": "chat-1",
                    "runId": "run-1",
                    "messages": [],
                    "state": {
                        "settings": {
                            "model_id": "gemini-3.8-flash",
                            "difficulty": "EXPERT",
                        }
                    },
                },
            )
            invalid_mode = client.post(
                "/api/v1/ag-ui",
                json={
                    "threadId": "chat-1",
                    "runId": "run-2",
                    "messages": [],
                    "state": {
                        "settings": {
                            "model_id": "gemini-3.8-flash",
                            "mode": "CHALLENGE_2",
                        }
                    },
                },
            )
        finally:
            app.dependency_overrides.clear()

    assert response.status_code == 422
    assert response.json()["detail"]
    assert invalid_difficulty.status_code == 422
    assert invalid_mode.status_code == 422
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_auth_checks_better_auth_session(monkeypatch) -> None:
    import hackathon.api.auth as auth_api

    requests = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.headers["cookie"].endswith("invalid"):
            return httpx.Response(401)
        return httpx.Response(204, headers={"x-auth-user": "github-user"})

    client_type = httpx.AsyncClient
    monkeypatch.setattr(
        auth_api.httpx,
        "AsyncClient",
        lambda **kwargs: client_type(transport=httpx.MockTransport(handle)),
    )
    monkeypatch.setattr(
        auth_api,
        "get_settings",
        lambda: Mock(is_local=False, AUTH_SERVICE_URL="http://localhost:3005"),
    )
    request = Request(
        {"type": "http", "headers": [(b"cookie", b"better-auth.session_token=signed")]}
    )

    assert await require_user(request) == "github-user"
    assert str(requests[0].url) == "http://localhost:3005/api/auth/authorize"
    assert requests[0].headers["cookie"] == "better-auth.session_token=signed"
    invalid = Request(
        {"type": "http", "headers": [(b"cookie", b"better-auth.session_token=invalid")]}
    )
    with pytest.raises(HTTPException) as exc:
        await require_user(invalid)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_local_requests_do_not_require_a_session(monkeypatch) -> None:
    import hackathon.api.auth as auth_api

    monkeypatch.setattr(auth_api, "get_settings", lambda: Mock(is_local=True))
    assert await require_user(Request({"type": "http", "headers": []})) == "local-user"


def test_agent_trace_carries_conversation_and_user_ids(monkeypatch) -> None:
    from ag_ui.core import RunAgentInput, TextMessageContentEvent
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    from hackathon.extensions.tracing import ContextualOpenInferenceSpanProcessor

    monkeypatch.setenv("AGENTGATEWAY_URL", "http://localhost:4000/v1")
    monkeypatch.setenv("AGENTGATEWAY_API_KEY", "test-gateway-key")
    monkeypatch.setenv("GOOGLE_STORAGE_BUCKET", "")
    monkeypatch.setenv("PRETTY_DOMAIN", "localhost:5000")
    monkeypatch.setenv("DB_USER", "app")
    monkeypatch.setenv("DB_PASSWORD", "devpassword")
    monkeypatch.setenv("DB_NAME", "app")
    get_settings.cache_clear()
    api_router = import_module("hackathon.api.router")
    attributes = {}

    class FakeAdapter:
        def __init__(self, **kwargs):
            pass

        @staticmethod
        def build_run_input(data):
            return RunAgentInput.model_validate_json(data)

        async def run_stream(self, **kwargs):
            attributes.update(get_attributes_from_context())
            yield TextMessageContentEvent(message_id="answer", delta="hel")
            yield TextMessageContentEvent(message_id="answer", delta="lo")

        def streaming_response(self, stream):
            async def encode():
                async for event in stream:
                    if isinstance(event, TextMessageContentEvent):
                        yield event.delta

            return StreamingResponse(encode())

    async def prepare_run(run_input, user_id):
        assert user_id == "github-user"
        return Mock(), {
            "mailbox": "finance-fast-test@internal.com",
            "difficulty": "EASY",
            "mode": "CHALLENGE_1",
        }

    span = Mock()
    span.get_span_context.return_value = api_router.trace.INVALID_SPAN_CONTEXT
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(ContextualOpenInferenceSpanProcessor())
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(api_router, "PlatformAGUIAdapter", FakeAdapter)
    monkeypatch.setattr(api_router.trace, "get_current_span", lambda context=None: span)
    monkeypatch.setattr(api_router.trace, "get_tracer", provider.get_tracer)
    app = FastAPI()
    app.include_router(api_router.router)
    app.dependency_overrides[require_user] = lambda: "github-user"
    app.dependency_overrides[api_router.get_ag_ui_run_preparer] = lambda: prepare_run

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/ag-ui",
            json={
                "threadId": "chat-123",
                "runId": "run-1",
                "messages": [{"id": "user-1", "role": "user", "content": "Hi"}],
            },
        )
        missing_session = client.post(
            "/api/v1/ag-ui",
            json={"threadId": "", "runId": "run-2", "messages": []},
        )

    assert response.status_code == 200
    assert response.text == "hello"
    assert missing_session.status_code == 422
    assert attributes["session.id"] == "chat-123"
    assert attributes["user.id"] == "github-user"
    assert span.set_attribute.call_args_list == [
        call("session.id", "chat-123"),
        call("user.id", "github-user"),
    ]
    [run_span] = exporter.get_finished_spans()
    assert run_span.name == "ag-ui run"
    assert run_span.attributes["openinference.span.kind"] == "CHAIN"
    assert '"content":"Hi"' in run_span.attributes["input.value"]
    assert run_span.attributes["output.value"] == "hello"
    assert run_span.attributes["session.id"] == "chat-123"
    assert run_span.attributes["user.id"] == "github-user"
    get_settings.cache_clear()


def test_local_tracing_exports_to_phoenix(monkeypatch) -> None:
    from hackathon.extensions import tracing

    exporter = Mock()
    processor = Mock()
    openinference_processor = Mock()
    exporter_factory = Mock(return_value=exporter)
    processor_factory = Mock(return_value=processor)
    configure = Mock()
    instrument_agent = Mock()
    instrument_fastapi = Mock()
    monkeypatch.setattr(tracing, "OTLPSpanExporter", exporter_factory)
    monkeypatch.setattr(tracing, "BatchSpanProcessor", processor_factory)
    monkeypatch.setattr(
        tracing,
        "ContextualOpenInferenceSpanProcessor",
        Mock(return_value=openinference_processor),
    )
    monkeypatch.setattr(tracing.logfire, "configure", configure)
    monkeypatch.setattr(tracing.logfire, "instrument_pydantic_ai", instrument_agent)
    monkeypatch.setattr(tracing.logfire, "instrument_fastapi", instrument_fastapi)

    app = FastAPI()
    tracing.configure_tracing(app, "http://phoenix:6006/v1/traces")

    exporter_factory.assert_called_once_with(endpoint="http://phoenix:6006/v1/traces")
    processor_factory.assert_called_once_with(exporter)
    assert configure.call_args.kwargs["send_to_logfire"] is False
    assert configure.call_args.kwargs["resource_attributes"] == {
        "openinference.project.name": "hackathon-2026"
    }
    assert configure.call_args.kwargs["additional_span_processors"] == [
        openinference_processor,
        processor,
    ]
    instrument_agent.assert_called_once_with(
        include_content=True, include_binary_content=False
    )
    instrument_fastapi.assert_called_once_with(
        app, excluded_urls=".*/api/health,.*/guardrails/request"
    )


def test_phoenix_spans_include_session_and_user() -> None:
    from openinference.instrumentation import using_attributes
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    from hackathon.extensions.tracing import ContextualOpenInferenceSpanProcessor

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(ContextualOpenInferenceSpanProcessor())
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    with (
        using_attributes(session_id="chat-123", user_id="github-user"),
        provider.get_tracer("test").start_as_current_span("agent"),
        provider.get_tracer("test").start_as_current_span("model"),
    ):
        pass

    for span in exporter.get_finished_spans():
        assert span.attributes["session.id"] == "chat-123"
        assert span.attributes["user.id"] == "github-user"


def test_agent_uses_gateway_for_model_requests(monkeypatch) -> None:
    monkeypatch.setenv("AGENTGATEWAY_URL", "http://agentgateway:3000/v1")
    monkeypatch.setenv("AGENTGATEWAY_API_KEY", "test-gateway-key")
    monkeypatch.setenv("GOOGLE_STORAGE_BUCKET", "")
    monkeypatch.setenv("PRETTY_DOMAIN", "localhost:5000")
    monkeypatch.setenv("DB_USER", "app")
    monkeypatch.setenv("DB_PASSWORD", "devpassword")
    monkeypatch.setenv("DB_NAME", "app")
    get_settings.cache_clear()

    agent = build_agent(
        {
            "user_id": "test",
            "current_date": datetime.now(),
            "mailbox": "test@internal.com",
            "difficulty": "EASY",
            "mode": "CHALLENGE_1",
        },
        "gemini-3.8-flash",
    )

    assert isinstance(agent.model, OpenAIChatModel)
    assert agent.model.model_name == "gemini-3.8-flash"
    assert str(agent.model.provider.base_url) == "http://agentgateway:3000/v1/"
    get_settings.cache_clear()
