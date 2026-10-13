import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from hackathon_deepteam.agui_target import AGUIResult
from hackathon_deepteam.api.auth import require_user
from hackathon_deepteam import runner
from hackathon_deepteam.api.runs import RunRequest
from hackathon_deepteam.config import Settings, get_settings
from hackathon_deepteam.extensions import configure_logging
from hackathon_deepteam.main import app


@pytest.fixture(autouse=True)
def test_settings(monkeypatch):
    monkeypatch.setenv("ENV", "prod")
    monkeypatch.setenv("LOG_FORMAT", "json")
    monkeypatch.setenv("AGENTGATEWAY_BASE_URL", "http://agentgateway:3000/v1")
    monkeypatch.setenv("AGENTGATEWAY_API_KEY", "test-key")
    get_settings.cache_clear()
    configure_logging()
    yield
    get_settings.cache_clear()


def test_run_passes_goal_target_and_session_to_deepteam(monkeypatch, caplog) -> None:
    seen = {}
    monkeypatch.setattr(runner, "LocalModel", lambda **kwargs: kwargs)
    monkeypatch.setattr(runner, "NoRefinementAttackEngine", lambda **kwargs: kwargs)
    monkeypatch.setattr(runner, "RBAC", lambda **kwargs: ("rbac", kwargs))
    monkeypatch.setattr(
        runner, "PromptLeakage", lambda **kwargs: ("prompt-leakage", kwargs)
    )

    async def fake_call(url, headers, state, prompt, *, turns, thread_id):
        seen.update(
            url=url, headers=headers, state=state, prompt=prompt, thread_id=thread_id
        )
        return AGUIResult(text="safe response", tool_names=["search"])

    def fake_red_team(**kwargs):
        seen.update(kwargs)
        turn = asyncio.run(kwargs["model_callback"]("attack"))
        seen["turn"] = turn
        return SimpleNamespace(
            model_dump=lambda **_: {
                "test_cases": [
                    {
                        "input": "attack",
                        "vulnerability": "Prompt Leakage",
                        "vulnerability_type": "prompt_leakage",
                        "attack_method": "Prompt Injection",
                        "score": 0,
                        "reason": "The attack succeeded",
                        "error": None,
                    }
                ]
            }
        )

    monkeypatch.setattr(runner, "call_agui", fake_call)
    monkeypatch.setattr(runner, "red_team", fake_red_team)
    app.dependency_overrides[require_user] = lambda: "user-1"

    response = TestClient(app).post(
        "/api/deepteam/runs",
        headers={"cookie": "session=test"},
        json={
            "goal": "Protect the finance assistant",
            "target_url": "http://backend:8000/api/v1/ag-ui",
            "target_headers": {"Cookie": "forged", "X-Test": "ok"},
        },
    )
    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["assessment"]["test_cases"][0]["score"] == 0
    assert response.json()["user_id"] == "user-1"
    assert seen["target_purpose"] == "Protect the finance assistant"
    assert seen["simulator_model"]["base_url"] == "http://agentgateway:3000/v1"
    assert seen["simulator_model"]["api_key"] == "test-key"
    assert seen["simulator_model"]["generation_kwargs"] == {
        "response_format": {"type": "json_object"}
    }
    assert seen["evaluation_model"] is seen["simulator_model"]
    assert seen["attack_engine"] == {"simulator_model": seen["simulator_model"]}
    assert seen["vulnerabilities"] == [
        ("rbac", {"types": ["role_bypass"]}),
        ("prompt-leakage", {"types": ["instructions"]}),
    ]
    assert seen["url"] == "http://backend:8000/api/v1/ag-ui"
    assert seen["headers"] == {"X-Test": "ok", "Cookie": "session=test"}
    assert seen["state"] == {
        "settings": {"modelId": "gemini-3.8-flash", "mode": "PLAYGROUND"}
    }
    assert seen["turn"].content == "safe response"
    assert seen["turn"].tools_called[0].name == "search"
    events = [record.msg for record in caplog.records if record.name == "deepteam"]
    assert [event["message"] for event in events] == [
        "run_started",
        "attempt_started",
        "attempt_completed",
        "test_case_scored",
        "run_completed",
    ]
    assert events[1]["prompt_preview"] == "attack"
    assert events[2]["response_preview"] == "safe response"
    assert events[3]["outcome"] == "attack_succeeded"
    assert events[4]["attack_succeeded"] == 1
    assert all(event["run_id"] == response.json()["run_id"] for event in events)
    assert "session=test" not in json.dumps(events)
    assert "test-key" not in json.dumps(events)


def test_logs_attempt_and_run_errors(monkeypatch, caplog) -> None:
    monkeypatch.setattr(runner, "LocalModel", lambda **kwargs: kwargs)
    monkeypatch.setattr(runner, "NoRefinementAttackEngine", lambda **kwargs: kwargs)
    monkeypatch.setattr(runner, "RBAC", lambda **kwargs: ("rbac", kwargs))
    monkeypatch.setattr(
        runner, "PromptLeakage", lambda **kwargs: ("prompt-leakage", kwargs)
    )

    async def failed_call(*args, **kwargs):
        raise RuntimeError("target failed")

    def fake_red_team(**kwargs):
        asyncio.run(kwargs["model_callback"]("attack"))

    monkeypatch.setattr(runner, "call_agui", failed_call)
    monkeypatch.setattr(runner, "red_team", fake_red_team)
    request = RunRequest(
        goal="Protect the assistant", target_url="http://backend:8000/api/v1/ag-ui"
    )
    settings = Settings(
        AGENTGATEWAY_BASE_URL="http://agentgateway:3000/v1",
        AGENTGATEWAY_API_KEY="test-key",
    )

    with pytest.raises(RuntimeError, match="target failed"):
        runner.run_deepteam(request, settings, {"Cookie": "session=test"}, "user-1")

    events = [record.msg for record in caplog.records if record.name == "deepteam"]
    assert [event["message"] for event in events][-2:] == [
        "attempt_failed",
        "run_failed",
    ]
    assert events[-2]["error_type"] == "RuntimeError"
    assert "session=test" not in json.dumps(events)


def test_run_requires_goal_and_http_target() -> None:
    app.dependency_overrides[require_user] = lambda: "user-1"
    response = TestClient(app).post(
        "/api/deepteam/runs", json={"goal": "", "target_url": "file:///etc/passwd"}
    )
    app.dependency_overrides.clear()
    assert response.status_code == 422


def test_run_requires_authentication() -> None:
    response = TestClient(app).post(
        "/api/deepteam/runs",
        json={"goal": "Test", "target_url": "http://backend:8000/api/v1/ag-ui"},
    )
    assert response.status_code == 401


def test_openapi_example_runs_challenge_one_locally(monkeypatch) -> None:
    import hackathon_deepteam.api.runs as runs

    example = app.openapi()["paths"]["/api/deepteam/runs"]["post"]["requestBody"][
        "content"
    ]["application/json"]["examples"]["challenge_one"]["value"]
    assert example["model"] == "gemini-3.8-flash"
    assert example["target_state"]["settings"]["mode"] == "CHALLENGE_1"
    assert example["goal"].startswith("Get the assistant to reveal")

    monkeypatch.setenv("ENV", "local")
    get_settings.cache_clear()
    monkeypatch.setattr(
        runs,
        "run_deepteam",
        lambda body, settings, headers, user_id: {
            "user_id": user_id,
            "headers": headers,
            "state": body.target_state,
        },
    )
    response = TestClient(app).post("/api/deepteam/runs", json=example)

    assert response.status_code == 200
    assert response.json()["user_id"] == "local-user"
    assert response.json()["headers"] == {}
    assert response.json()["state"]["settings"]["mode"] == "CHALLENGE_1"


def test_run_rejects_unlisted_target(monkeypatch) -> None:
    monkeypatch.setenv("AGENTGATEWAY_BASE_URL", "http://agentgateway:3000/v1")
    monkeypatch.setenv("AGENTGATEWAY_API_KEY", "test-key")
    app.dependency_overrides[require_user] = lambda: "user-1"
    response = TestClient(app).post(
        "/api/deepteam/runs",
        headers={"cookie": "session=test"},
        json={"goal": "Test", "target_url": "http://other:8000/api/v1/ag-ui"},
    )
    app.dependency_overrides.clear()
    assert response.status_code == 400
