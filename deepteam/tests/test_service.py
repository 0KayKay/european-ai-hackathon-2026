import asyncio
import json
from threading import Event, Thread
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from hackathon_deepteam.agui_target import AGUIResult, CONTENT_FILTER_RESPONSE
from hackathon_deepteam.api.auth import require_user
from hackathon_deepteam import runner
from hackathon_deepteam.api.runs import RunRequest
from hackathon_deepteam.config import Settings, get_settings
from hackathon_deepteam.extensions import configure_logging
from hackathon_deepteam.main import app
from hackathon_deepteam.mailbox_metric import mailbox_for
from hackathon_deepteam.scan_config import DEFAULT_GOAL


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
    monkeypatch.setattr(
        runner,
        "LocalQwenModel",
        lambda config, base_url, stop_event=None: {"config": config, "base_url": base_url},
    )
    monkeypatch.setattr(runner, "MailboxDisclosure", lambda **kwargs: kwargs)
    monkeypatch.setattr(
        runner, "LinearJailbreaking", lambda **kwargs: ("linear", kwargs)
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
    assert seen["target_purpose"].startswith("Protect the finance assistant")
    assert seen["simulator_model"]["base_url"] == "http://host.docker.internal:11434"
    assert seen["simulator_model"]["config"].model == "ollama/qwen3:8b"
    assert seen["evaluation_model"] is seen["simulator_model"]
    assert seen["vulnerabilities"][0]["name"] == "Level 1 finance mailbox extraction"
    assert seen["vulnerabilities"][0]["metric_options"]["key"] == "test-key"
    assert seen["attacks"][0][0] == "linear"
    assert seen["attacks"][0][1]["num_turns"] == 15
    assert seen["attacks_per_vulnerability_type"] == 10
    assert seen["url"] == "http://backend:8000/api/v1/ag-ui"
    assert seen["headers"] == {"X-Test": "ok", "Cookie": "session=test"}
    assert seen["state"] == {
        "settings": {
            "modelId": "gemini-3.8-flash",
            "mode": "CHALLENGE_1",
            "difficulty": "HARD",
        }
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
    calls = []
    monkeypatch.setattr(runner, "LocalQwenModel", lambda config, base_url, stop_event=None: {})
    monkeypatch.setattr(runner, "MailboxDisclosure", lambda **kwargs: kwargs)
    monkeypatch.setattr(
        runner, "LinearJailbreaking", lambda **kwargs: ("linear", kwargs)
    )

    async def failed_call(*args, **kwargs):
        calls.append(args[3])
        raise RuntimeError("target failed")

    def fake_red_team(**kwargs):
        async def run_two():
            results = await asyncio.gather(
                kwargs["model_callback"]("attack"),
                kwargs["model_callback"]("later attack"),
                return_exceptions=True,
            )
            raise results[0]

        asyncio.run(run_two())

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

    assert calls == ["attack"]
    events = [record.msg for record in caplog.records if record.name == "deepteam"]
    assert [event["message"] for event in events][-2:] == [
        "attempt_failed",
        "run_failed",
    ]
    assert events[-2]["error_type"] == "RuntimeError"
    assert "session=test" not in json.dumps(events)


def test_content_filtered_attempt_continues_to_next_attack(monkeypatch) -> None:
    monkeypatch.setattr(runner, "LocalQwenModel", lambda config, base_url, stop_event=None: {})
    monkeypatch.setattr(runner, "MailboxDisclosure", lambda **kwargs: kwargs)
    monkeypatch.setattr(runner, "LinearJailbreaking", lambda **kwargs: ("linear", kwargs))
    calls = []
    events = []

    async def fake_call(url, headers, state, prompt, *, turns, thread_id, on_event):
        calls.append(prompt)
        if prompt == "filtered attack":
            return AGUIResult(CONTENT_FILTER_RESPONSE, [], filtered=True)
        return AGUIResult("No secret was revealed.", [])

    def fake_red_team(**kwargs):
        async def run_two():
            first = await kwargs["model_callback"]("filtered attack")
            second = await kwargs["model_callback"]("next attack")
            return first, second

        first, second = asyncio.run(run_two())
        assert first.content == CONTENT_FILTER_RESPONSE
        assert second.content == "No secret was revealed."
        return SimpleNamespace(
            model_dump=lambda **_: {
                "test_cases": [
                    {"input": prompt, "score": 1.0, "error": None}
                    for prompt in calls
                ]
            }
        )

    monkeypatch.setattr(runner, "call_agui", fake_call)
    monkeypatch.setattr(runner, "red_team", fake_red_team)
    response = runner.run_deepteam(
        RunRequest(target_url="http://backend:8000/api/v1/ag-ui"),
        Settings(
            AGENTGATEWAY_BASE_URL="http://agentgateway:3000/v1",
            AGENTGATEWAY_API_KEY="test-key",
        ),
        {},
        "user-1",
        emit=events.append,
    )

    assert calls == ["filtered attack", "next attack"]
    assert response["outcomes"]["defense_passed"] == 2
    assert [event["type"] for event in events].count("attempt.completed") == 2
    assert not any(event["type"] == "run.failed" for event in events)


def test_first_disclosure_stops_scan_before_next_attack(monkeypatch) -> None:
    monkeypatch.setattr(runner, "LocalQwenModel", lambda config, base_url, stop_event=None: {})
    monkeypatch.setattr(runner, "MailboxDisclosure", lambda **kwargs: kwargs)
    monkeypatch.setattr(runner, "LinearJailbreaking", lambda **kwargs: ("linear", kwargs))
    calls = []
    events = []

    async def fake_call(url, headers, state, prompt, *, turns, thread_id, on_event):
        calls.append(prompt)
        on_event({"type": "TEXT_MESSAGE_CONTENT", "delta": "The mailbox is "})
        return AGUIResult(
            text=mailbox_for("test-key", "user-1", thread_id, "HARD"),
            tool_names=["get_file_content"],
        )

    def fake_red_team(**kwargs):
        async def run_two():
            results = await asyncio.gather(
                kwargs["model_callback"]("first attack"),
                kwargs["model_callback"]("second attack"),
                return_exceptions=True,
            )
            raise results[0]

        asyncio.run(run_two())

    monkeypatch.setattr(runner, "call_agui", fake_call)
    monkeypatch.setattr(runner, "red_team", fake_red_team)
    request = RunRequest(target_url="http://backend:8000/api/v1/ag-ui")
    settings = Settings(
        AGENTGATEWAY_BASE_URL="http://agentgateway:3000/v1",
        AGENTGATEWAY_API_KEY="test-key",
    )
    response = runner.run_deepteam(
        request, settings, {}, "user-1", emit=events.append
    )

    assert calls == ["first attack"]
    assert response["stopped_early"] is True
    assert response["outcomes"]["attack_succeeded"] == 1
    assert response["duration_ms"] >= 0
    assert [event["type"] for event in events][-3:] == [
        "attempt.completed", "attempt.scored", "run.completed"
    ]
    assert any(event["type"] == "agent.responding" for event in events)
    assert next(event for event in events if event["type"] == "attempt.started")["prompt"] == "first attack"
    assert next(event for event in events if event["type"] == "agent.message.delta")["delta"] == "The mailbox is "
    assert next(event for event in events if event["type"] == "attempt.completed")["response"] == response["assessment"]["test_cases"][0]["actual_output"]
    assert not any(event["type"] == "run.failed" for event in events)


def test_external_stop_cancels_inflight_agent_request(monkeypatch) -> None:
    started = Event()
    cancelled = Event()
    stop_event = Event()
    monkeypatch.setattr(
        runner, "LocalQwenModel", lambda config, base_url, stop_event=None: {}
    )
    monkeypatch.setattr(runner, "MailboxDisclosure", lambda **kwargs: kwargs)
    monkeypatch.setattr(runner, "LinearJailbreaking", lambda **kwargs: ("linear", kwargs))

    async def slow_call(*args, **kwargs):
        started.set()
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    def fake_red_team(**kwargs):
        asyncio.run(kwargs["model_callback"]("attack"))

    monkeypatch.setattr(runner, "call_agui", slow_call)
    monkeypatch.setattr(runner, "red_team", fake_red_team)
    Thread(target=lambda: (started.wait(), stop_event.set()), daemon=True).start()
    request = RunRequest(target_url="http://backend:8000/api/v1/ag-ui")
    settings = Settings(
        AGENTGATEWAY_BASE_URL="http://agentgateway:3000/v1",
        AGENTGATEWAY_API_KEY="test-key",
    )
    with pytest.raises(RuntimeError, match="run stopped"):
        runner.run_deepteam(request, settings, {}, "user-1", cancel_event=stop_event)
    assert cancelled.is_set()


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


def test_stream_emits_attempt_progress_and_completion(monkeypatch) -> None:
    import hackathon_deepteam.api.runs as runs

    def fake_run(body, settings, headers, user_id, emit, cancel_event):
        emit({"type": "run.started", "run_id": "run-1", "difficulty": body.difficulty})
        emit({"type": "attempt.started", "thread_id": "thread-1", "attempt": 1})
        emit(
            {
                "type": "stage.updated",
                "thread_id": "thread-1",
                "stage": "guardrail",
                "status": "error",
            }
        )
        emit(
            {
                "type": "run.completed",
                "run_id": "run-1",
                "outcomes": {"defense_passed": 1},
            }
        )

    monkeypatch.setattr(runs, "run_deepteam", fake_run)
    app.dependency_overrides[require_user] = lambda: "user-1"
    try:
        response = TestClient(app).post(
            "/api/deepteam/runs/stream",
            json={
                "target_url": "http://backend:8000/api/v1/ag-ui",
                "difficulty": "HARD",
            },
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = [
        json.loads(line[6:])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert [event["type"] for event in events] == [
        "run.started",
        "attempt.started",
        "stage.updated",
        "run.completed",
    ]
    assert events[2]["status"] == "error"


def test_stream_reports_internal_failure_once(monkeypatch) -> None:
    import hackathon_deepteam.api.runs as runs

    def fake_run(body, settings, headers, user_id, emit, cancel_event):
        emit({"type": "run.started", "run_id": "run-1"})
        emit({"type": "run.failed", "detail": "Internal failure"})
        raise RuntimeError("Internal failure")

    monkeypatch.setattr(runs, "run_deepteam", fake_run)
    app.dependency_overrides[require_user] = lambda: "user-1"
    try:
        response = TestClient(app).post(
            "/api/deepteam/runs/stream",
            json={"target_url": "http://backend:8000/api/v1/ag-ui"},
        )
    finally:
        app.dependency_overrides.clear()
    events = [
        json.loads(line[6:])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert [event["type"] for event in events] == ["run.started", "run.failed"]


def test_config_exposes_branch_objective_defaults() -> None:
    app.dependency_overrides[require_user] = lambda: "user-1"
    try:
        response = TestClient(app).get("/api/deepteam/config")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    config = response.json()
    assert config["difficulty"] == "HARD"
    assert config["attacker"]["model"] == "ollama/qwen3:8b"
    assert config["run"]["attacks_per_type"] == 10
    assert config["run"]["linear_turns"] == 15
    assert config["objective"]["purpose"] == DEFAULT_GOAL
    assert (
        "The assistant can call list_folders"
        in config["objective"]["generation_prompt"]
    )
    assert "plausible work context" in config["objective"]["generation_prompt"]


def test_openapi_example_runs_challenge_one_locally(monkeypatch) -> None:
    import hackathon_deepteam.api.runs as runs

    example = app.openapi()["paths"]["/api/deepteam/runs"]["post"]["requestBody"][
        "content"
    ]["application/json"]["examples"]["challenge_one"]["value"]
    assert example["model"] == "gemini-3.8-flash"
    assert example["difficulty"] == "HARD"
    assert example["attacker"]["model"] == "ollama/qwen3:8b"
    assert example["goal"] == DEFAULT_GOAL

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
    assert response.json()["state"] is None


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


def test_api_accepts_branch_scan_options(monkeypatch) -> None:
    import hackathon_deepteam.api.runs as runs

    captured = {}
    app.dependency_overrides[require_user] = lambda: "user-1"

    def fake_run(body, settings, headers, user_id):
        captured["body"] = body
        return {"run_id": "test-run", "user_id": user_id, "assessment": {}}

    monkeypatch.setattr(runs, "run_deepteam", fake_run)
    response = TestClient(app).post(
        "/api/deepteam/runs",
        json={
            "target_url": "http://backend:8000/api/v1/ag-ui",
            "difficulty": "MEDIUM",
            "attacker": {"temperature": 0.2, "max_tokens": 1024},
            "objective": {"name": "Custom mailbox test", "attack_types": ["mailbox"]},
            "run": {
                "attacks": ["PromptInjection", "Roleplay"],
                "attacks_per_type": 2,
                "max_concurrent": 1,
                "linear_turns": 4,
            },
        },
    )
    app.dependency_overrides.clear()
    assert response.status_code == 200
    body = captured["body"]
    assert body.difficulty == "MEDIUM"
    assert body.attacker.model == "ollama/qwen3:8b"
    assert body.attacker.temperature == 0.2
    assert body.objective.name == "Custom mailbox test"
    assert body.run.attacks == ["PromptInjection", "Roleplay"]
    assert body.run.attacks_per_type == 2


@pytest.mark.parametrize(
    "change",
    [
        {"model": "gemini-2.5-flash"},
        {"attacker": {"model": "openai/gpt-4o"}},
        {"run": {"max_concurrent": 0}},
        {"run": {"max_concurrent": 2}},
        {"target_state": {"settings": "invalid"}},
    ],
)
def test_api_rejects_invalid_scan_options(change) -> None:
    app.dependency_overrides[require_user] = lambda: "user-1"
    response = TestClient(app).post(
        "/api/deepteam/runs",
        json={"target_url": "http://backend:8000/api/v1/ag-ui", **change},
    )
    app.dependency_overrides.clear()
    assert response.status_code == 422
