"""Contract checks for the backend-facing DeepTeam arena API."""

import json
from types import SimpleNamespace

import httpx
import openai
import pytest
from fastapi.testclient import TestClient

from hackathon_deepteam import arena
from hackathon_deepteam.main import app

MODEL = {"model": "test", "endpoint": "http://agentgateway:3000/v1", "params": {}}
OBJECTIVE = {
    "purpose": "<objective purpose>",
    "criteria": "<objective criteria>",
    "attacker_knowledge": "<attacker knowledge>",
}


def body(channel: str = "indirect") -> dict:
    return {
        "run_id": "run-1",
        "level_id": "email-review",
        "attacks": [{"type": "prompt_injection", "count": 2}],
        "objective": OBJECTIVE,
        "surface": {
            "channel": channel,
            "injection": {
                "point": "tool_output" if channel == "indirect" else "user_message",
                "description": "An inbox email",
                "fields": (
                    [
                        {"name": "subject", "max_length": 300},
                        {"name": "body", "max_length": 10000},
                    ]
                    if channel == "indirect"
                    else []
                ),
            },
        },
        "models": {role: MODEL for role in ("attacker", "simulator", "evaluator")},
    }


def lines(response):
    return [json.loads(line) for line in response.text.splitlines()]


def test_generate_requires_internal_key(monkeypatch):
    monkeypatch.setattr(
        arena, "get_settings", lambda: SimpleNamespace(AGENTGATEWAY_API_KEY="secret")
    )
    response = TestClient(app).post("/internal/arena/generate", json=body())
    assert response.status_code == 401


def test_model_for_requests_json_and_keeps_gateway_key_private(monkeypatch):
    monkeypatch.setattr(
        arena,
        "get_settings",
        lambda: SimpleNamespace(
            AGENTGATEWAY_API_KEY="gateway-secret",
            AGENTGATEWAY_BASE_URL="http://agentgateway:3000/v1",
        ),
    )
    calls = []
    monkeypatch.setattr(arena, "ArenaLocalModel", lambda **kwargs: calls.append(kwargs))
    arena.model_for(arena.ModelSpec.model_validate(MODEL), "run-1")
    assert calls[-1]["api_key"] == "gateway-secret"
    assert calls[-1]["generation_kwargs"]["response_format"] == {"type": "json_object"}
    arena.model_for(
        arena.ModelSpec(
            model="test", endpoint="https://example.org/v1", params={"max_tokens": 128}
        ),
        "run-1",
    )
    assert calls[-1]["api_key"] == "unused"
    assert calls[-1]["default_headers"] == {}


def test_model_for_maps_leonardo_spec_and_rejects_gateway_lookalikes(monkeypatch):
    monkeypatch.setattr(
        arena,
        "get_settings",
        lambda: SimpleNamespace(
            AGENTGATEWAY_API_KEY="gateway-secret",
            AGENTGATEWAY_BASE_URL="http://agentgateway:3000/v1",
        ),
    )
    calls = []
    monkeypatch.setattr(arena, "ArenaLocalModel", lambda **kwargs: calls.append(kwargs))
    leonardo = {
        "temperature": 0,
        "max_tokens": 512,
        "extra": {"chat_template_kwargs": {"enable_thinking": False}},
    }
    arena.model_for(
        arena.ModelSpec(
            model="qwen3-235b-a22b-fp8-leonardo",
            endpoint="http://agentgateway:3000/v1/",
            params=leonardo,
        ),
        "run-1",
    )
    assert calls[-1]["model"] == "qwen3-235b-a22b-fp8-leonardo"
    assert calls[-1]["api_key"] == "gateway-secret"
    assert calls[-1]["temperature"] == 0
    assert calls[-1]["generation_kwargs"] == {
        "max_tokens": 512,
        "extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
        "response_format": {"type": "json_object"},
    }
    for endpoint in (
        "http://agentgateway:3001/v1",
        "http://agentgateway:3000/v1/chat",
        "http://agentgateway:3000/v1.example.org",
        "http://agentgateway.example.org:3000/v1",
        "http://user@agentgateway:3000/v1",
        "https://agentgateway:3000/v1",
        "http://agentgateway:3000/v1?next=http://example.org",
    ):
        arena.model_for(
            arena.ModelSpec(model="test", endpoint=endpoint, params=leonardo), "run-1"
        )
        assert calls[-1]["api_key"] == "unused", endpoint
        assert calls[-1]["default_headers"] == {}, endpoint


@pytest.mark.asyncio
async def test_structured_model_request_uses_schema_and_rejects_truncation(monkeypatch):
    calls = []
    finish_reason = "stop"

    async def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason=finish_reason,
                    message=SimpleNamespace(content='{"input":"hello"}'),
                )
            ]
        )

    model = arena.ArenaLocalModel(
        model="test",
        api_key="unused",
        base_url="http://localhost:11434/v1",
        generation_kwargs={
            "max_tokens": 512,
            "reasoning_effort": "none",
            "response_format": {"type": "json_object"},
        },
    )
    monkeypatch.setattr(
        model,
        "load_model",
        lambda async_mode: SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        ),
    )
    result, _ = await model.a_generate("Return an input", schema=arena.InitialAttack)
    assert result.input == "hello"
    assert calls[0]["response_format"]["type"] == "json_schema"
    assert calls[0]["response_format"]["json_schema"]["schema"]["required"] == ["input"]
    assert calls[0]["reasoning_effort"] == "none"
    finish_reason = "length"
    with pytest.raises(ValueError, match="token limit"):
        await model.a_generate("Return an input", schema=arena.InitialAttack)


@pytest.mark.asyncio
async def test_structured_model_request_retries_transient_errors(monkeypatch):
    from deepeval.config.settings import get_settings as deepeval_settings

    monkeypatch.setattr(deepeval_settings(), "DEEPEVAL_RETRY_CAP_SECONDS", 0)
    attempts = []

    async def create(**kwargs):
        attempts.append(kwargs)
        if len(attempts) == 1:
            raise openai.APIConnectionError(request=httpx.Request("POST", "/"))
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(content='{"input":"<attack 1>"}'),
                )
            ]
        )

    model = arena.ArenaLocalModel(
        model="test", api_key="unused", base_url="http://localhost:11434/v1"
    )
    monkeypatch.setattr(
        model,
        "load_model",
        lambda async_mode: SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        ),
    )
    result, _ = await model.a_generate("Return an input", schema=arena.InitialAttack)
    assert result.input == "<attack 1>"
    assert len(attempts) == 2


def test_generate_streams_cases_with_contract_shape(monkeypatch):
    monkeypatch.setattr(
        arena, "get_settings", lambda: SimpleNamespace(AGENTGATEWAY_API_KEY="secret")
    )
    calls = []

    async def fake_one(request, attack_type):
        calls.append(attack_type)
        return {
            "attack_type": attack_type,
            "content": {"subject": f"<subject {len(calls)}>", "body": "<attack>"},
        }

    monkeypatch.setattr(arena, "generate_one", fake_one)
    response = TestClient(app).post(
        "/internal/arena/generate",
        json=body(),
        headers={"Authorization": "Bearer secret"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    events = lines(response)
    assert [event["type"] for event in events] == ["case", "case", "done"]
    assert events[-1]["count"] == 2
    assert events[0]["case"]["content"]["subject"] == "<subject 1>"


@pytest.mark.asyncio
async def test_generate_one_shapes_indirect_and_direct_attacks(monkeypatch):
    class FakeModel:
        async def a_generate(self, prompt, schema):
            if schema is arena.InitialAttack:
                return arena.InitialAttack(input="<baseline>"), 0
            assert "Attack goal: <objective criteria>" in prompt
            return (
                arena.InjectionContent(
                    content={"subject": "<subject>", "body": "<enhanced>"}
                ),
                0,
            )

    class FakeAttack:
        async def a_enhance(self, input, model):
            assert input == "<baseline>"
            return "<enhanced>"

    monkeypatch.setattr(arena, "model_for", lambda *_: FakeModel())
    monkeypatch.setitem(arena.SINGLE_ATTACKS, "prompt_injection", FakeAttack)
    indirect = arena.GenerateRequest.model_validate(body())
    direct = arena.GenerateRequest.model_validate(body("direct"))
    assert await arena.generate_one(indirect, "prompt_injection") == {
        "attack_type": "prompt_injection",
        "content": {"subject": "<subject>", "body": "<enhanced>"},
    }
    assert await arena.generate_one(direct, "prompt_injection") == {
        "attack_type": "prompt_injection",
        "input": "<enhanced>",
    }


@pytest.mark.asyncio
async def test_generate_one_accepts_flat_injection_fields_from_model(monkeypatch):
    class FakeModel:
        async def a_generate(self, prompt, schema):
            if schema is arena.InitialAttack:
                return schema(input="<baseline>"), 0
            return schema.model_validate(
                {"subject": "<subject>", "body": "<enhanced>"}
            ), 0

    class FakeAttack:
        async def a_enhance(self, input, model):
            return "<enhanced>"

    monkeypatch.setattr(arena, "model_for", lambda *_: FakeModel())
    monkeypatch.setitem(arena.SINGLE_ATTACKS, "prompt_injection", FakeAttack)

    result = await arena.generate_one(
        arena.GenerateRequest.model_validate(body()), "prompt_injection"
    )
    assert result["content"] == {"subject": "<subject>", "body": "<enhanced>"}


def test_injection_content_keeps_field_types_strict():
    assert arena.InjectionContent.model_validate(
        {"additional_notes": "<attack>"}
    ).content == {"additional_notes": "<attack>"}
    with pytest.raises(arena.ValidationError):
        arena.InjectionContent.model_validate({"subject": "ok", "body": 42})


def test_multi_turn_stops_after_backend_success(monkeypatch):
    monkeypatch.setattr(
        arena, "get_settings", lambda: SimpleNamespace(AGENTGATEWAY_API_KEY="secret")
    )

    class FakeModel:
        def __init__(self):
            self.calls = 0

        async def a_generate(self, prompt, schema):
            self.calls += 1
            if self.calls == 1:
                schema.model_validate({"error": "refused first prompt"})
            return arena.InitialAttack(input="<first attack>"), 0

    class FakeAttack:
        def __init__(self, **kwargs):
            pass

        async def _a_get_turns(self, *, model_callback, **kwargs):
            await model_callback("<first attack>")
            await model_callback("<second attack>")
            await model_callback("<should not be sent>")

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"reply": "<agent reply>", "stop": True}

        def raise_for_status(self):
            pass

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def post(self, url, *, json, headers):
            assert headers == {"x-arena-token": "callback-secret"}
            return FakeResponse()

    monkeypatch.setattr(arena, "model_for", lambda *_: FakeModel())
    monkeypatch.setitem(arena.MULTI_ATTACKS, "linear_jailbreaking", FakeAttack)
    monkeypatch.setattr(arena.httpx, "AsyncClient", lambda **kwargs: FakeClient())
    request = {
        "case_id": "case-1",
        "attack_type": "linear_jailbreaking",
        "max_turns": 5,
        "objective": OBJECTIVE,
        "models": {role: MODEL for role in ("attacker", "simulator", "evaluator")},
        "callback": {
            "url": "http://backend:8000/api/v1/arena/internal/cases/case-1/turn",
            "token": "callback-secret",
        },
    }
    response = TestClient(app).post(
        "/internal/arena/multi-turn",
        json=request,
        headers={"Authorization": "Bearer secret"},
    )
    assert response.status_code == 200
    assert lines(response) == [
        {
            "type": "turn",
            "turn": 1,
            "attacker_message": "<first attack>",
            "reply": "<agent reply>",
        },
        {"type": "done", "turns": 1},
    ]


def test_multi_turn_continues_when_deepteam_ends_early(monkeypatch):
    monkeypatch.setattr(
        arena, "get_settings", lambda: SimpleNamespace(AGENTGATEWAY_API_KEY="secret")
    )

    class FakeModel:
        def __init__(self):
            self.calls = 0

        async def a_generate(self, prompt, schema):
            self.calls += 1
            return arena.InitialAttack(
                input="<message 1>" if self.calls <= 2 else "<message 2>"
            ), 0

    class FakeAttack:
        def __init__(self, **kwargs):
            pass

        async def _a_get_turns(self, *, model_callback, **kwargs):
            await model_callback("<message 1>")
            return []  # DeepTeam stopped although the backend did not.

    class FakeResponse:
        def __init__(self, stop):
            self.status_code = 200
            self.stop = stop

        def json(self):
            return {"reply": "<reply>", "stop": self.stop}

        def raise_for_status(self):
            pass

    class FakeClient:
        calls = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def post(self, url, *, json, headers):
            self.calls += 1
            return FakeResponse(self.calls == 2)

    client = FakeClient()
    monkeypatch.setattr(arena, "model_for", lambda *_: FakeModel())
    monkeypatch.setitem(arena.MULTI_ATTACKS, "linear_jailbreaking", FakeAttack)
    monkeypatch.setattr(arena.httpx, "AsyncClient", lambda **kwargs: client)
    request = {
        "case_id": "case-1",
        "attack_type": "linear_jailbreaking",
        "max_turns": 5,
        "objective": OBJECTIVE,
        "models": {role: MODEL for role in ("attacker", "simulator", "evaluator")},
        "callback": {
            "url": "http://backend:8000/api/v1/arena/internal/cases/case-1/turn",
            "token": "callback-secret",
        },
    }
    response = TestClient(app).post(
        "/internal/arena/multi-turn",
        json=request,
        headers={"Authorization": "Bearer secret"},
    )
    assert response.status_code == 200
    assert [(event["type"], event.get("turn")) for event in lines(response)] == [
        ("turn", 1),
        ("turn", 2),
        ("done", None),
    ]
    assert lines(response)[-1]["turns"] == 2


@pytest.mark.asyncio
async def test_repeated_followups_end_case_without_driver_error(monkeypatch):
    class RepeatingModel:
        async def a_generate(self, prompt, schema):
            return arena.InitialAttack(input="<same message>"), 0

    class OneTurnAttack:
        def __init__(self, **kwargs):
            pass

        async def _a_get_turns(self, *, model_callback, **kwargs):
            await model_callback("<same message>")

    class Reply:
        status_code = 200

        def json(self):
            return {"reply": "<try again>", "stop": False}

        def raise_for_status(self):
            pass

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def post(self, url, *, json, headers):
            return Reply()

    monkeypatch.setattr(arena, "model_for", lambda *_: RepeatingModel())
    monkeypatch.setitem(arena.MULTI_ATTACKS, "linear_jailbreaking", OneTurnAttack)
    monkeypatch.setattr(arena.httpx, "AsyncClient", lambda **kwargs: Client())
    request = arena.MultiTurnRequest.model_validate(
        {
            "case_id": "case-1",
            "attack_type": "linear_jailbreaking",
            "max_turns": 5,
            "objective": OBJECTIVE,
            "models": {role: MODEL for role in ("attacker", "simulator", "evaluator")},
            "callback": {
                "url": "http://backend:8000/api/v1/arena/internal/cases/case-1/turn",
                "token": "callback-secret",
            },
        }
    )
    events = [event async for event in arena.drive_one(request)]
    assert [(event["type"], event.get("turn")) for event in events] == [
        ("turn", 1),
        ("done", None),
    ]
    assert events[-1]["turns"] == 1


def multi_turn_request(objective: dict) -> arena.MultiTurnRequest:
    return arena.MultiTurnRequest.model_validate(
        {
            "case_id": "case-1",
            "attack_type": "linear_jailbreaking",
            "max_turns": 3,
            "objective": objective,
            "models": {role: MODEL for role in ("attacker", "simulator", "evaluator")},
            "callback": {
                "url": "http://backend:8000/api/v1/arena/internal/cases/case-1/turn",
                "token": "callback-secret",
            },
        }
    )


async def attacker_prompts(monkeypatch, objective: dict) -> list[str]:
    """Drive one case with mocks and return every prompt sent to the attacker model."""
    prompts = []

    class RecordingModel:
        async def a_generate(self, prompt, schema):
            prompts.append(prompt)
            return arena.InitialAttack(input=f"<attack {len(prompts)}>"), 0

    class EarlyStopAttack:
        def __init__(self, **kwargs):
            pass

        async def _a_get_turns(self, *, model_callback, turns, **kwargs):
            await model_callback(turns[0].content)

    class Reply:
        status_code = 200

        def json(self):
            return {"reply": "<agent reply>", "stop": False}

        def raise_for_status(self):
            pass

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def post(self, url, *, json, headers):
            return Reply()

    monkeypatch.setattr(arena, "model_for", lambda *_: RecordingModel())
    monkeypatch.setitem(arena.MULTI_ATTACKS, "linear_jailbreaking", EarlyStopAttack)
    monkeypatch.setattr(arena.httpx, "AsyncClient", lambda **kwargs: Client())
    events = [event async for event in arena.drive_one(multi_turn_request(objective))]
    assert [event.get("attacker_message") for event in events] == [
        "<attack 1>",
        "<attack 2>",
        "<attack 3>",
        None,
    ]
    return prompts


@pytest.mark.asyncio
async def test_multi_turn_prompts_follow_request_objective(monkeypatch):
    other = {
        "purpose": "<other purpose>",
        "criteria": "<other criteria>",
        "attacker_knowledge": "<other knowledge>",
    }
    first = await attacker_prompts(monkeypatch, OBJECTIVE)
    second = await attacker_prompts(monkeypatch, other)

    # One opening prompt, then the driver's own follow-ups after DeepTeam stopped.
    assert len(first) == len(second) == 3
    for prompt in first:
        assert "Purpose: <objective purpose>" in prompt
        assert "Goal: <objective criteria>" in prompt
        assert "What you know: <attacker knowledge>" in prompt
    for prompt in second:
        assert "<other purpose>" in prompt
        assert "<other criteria>" in prompt
        assert "<other knowledge>" in prompt
        assert "<objective" not in prompt
    assert "User: <attack 1>" in first[1]
    assert all(a != b for a, b in zip(first, second, strict=True))
