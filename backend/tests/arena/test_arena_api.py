from fastapi import FastAPI
from fastapi.testclient import TestClient

from hackathon.api.auth import require_user
from hackathon.arena.api import get_level_catalog, router
from hackathon.arena.config.loader import LEVELS_ROOT
from hackathon.config import get_settings


def client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_user] = lambda: "github-user"
    return TestClient(app)


def test_list_levels():
    response = client().get("/api/v1/arena/levels")
    assert response.status_code == 200
    first = response.json()[0]
    assert set(first) == {
        "id",
        "title",
        "description",
        "channel",
        "turns",
        "difficulties",
    }
    assert (first["title"], first["difficulties"]) == (
        "Level 1 · Hidden Mailbox",
        ["easy", "medium", "hard"],
    )
    assert first["description"]
    assert [
        (level["id"], level["channel"], level["turns"]) for level in response.json()
    ] == [
        ("hidden-mailbox", "direct", "multi"),
        ("email-review", "indirect", "single"),
        ("offer-review", "indirect", "single"),
        ("hidden-mailbox-output-judge", "direct", "multi"),
    ]


def test_level_view():
    response = client().get("/api/v1/arena/levels/email-review/hard")
    assert response.status_code == 200
    body = response.json()
    assert (body["level"], body["difficulty"], body["title"]) == (
        "email-review",
        "hard",
        "Level 2 · Email Review",
    )
    assert body["difficulties"] == ["easy", "medium", "hard"]
    assert body["surface"]["channel"] == "indirect"
    assert body["surface"]["user_task"] == "Please process my email inbox."
    assert body["graph"]["surface"] == {
        "channel": "indirect",
        "turns": "single",
        "max_turns": 1,
    }
    assert {node["id"] for node in body["graph"]["nodes"]} >= {
        "injection",
        "user_task",
        "guardrail:send-email-judge",
    }
    assert [model["role"] for model in body["models"]] == [
        "agent",
        "guardrail:output-judge",
        "guardrail:send-email-judge",
        "red_team.attacker",
        "red_team.simulator",
        "red_team.evaluator",
        "green_team.generator",
    ]
    assert body["models"][0] == {
        "role": "agent",
        "node": "agent",
        "model": "qwen3-235b-a22b-fp8-leonardo",
        "endpoint": "agentgateway",
        "params": {"max_tokens": 512, "extra": {"chat_template_kwargs": {"enable_thinking": False}}},
    }
    files = body["source"]["files"]
    assert files[0]["path"] == "email-review/level.yaml"
    paths = [file["path"] for file in files]
    assert {
        "email-review/prompts/send_email_judge.md",
        "shared/prompts/judge_system.md",
    } <= set(paths)
    for file in files:
        assert file["content"] == (LEVELS_ROOT / file["path"]).read_text()
    assert body["red_team"]["cases"] == 10
    assert body["red_team"]["attacks"][0] == {
        "type": "prompt_injection",
        "title": "Prompt injection",
        "turns": "single",
        "attempts": 5,
    }
    assert body["red_team"]["objective"]["criteria"]
    assert body["green_team"]["cases"] == 10
    assert (
        body["green_team"]["guidance"]["file"]
        == "email-review/prompts/green_guidance.md"
    )


def test_every_level_view_renders():
    api = client()
    for level in api.get("/api/v1/arena/levels").json():
        for difficulty in level["difficulties"]:
            response = api.get(f"/api/v1/arena/levels/{level['id']}/{difficulty}")
            assert response.status_code == 200, (level["id"], difficulty)


def test_unknown_level_or_difficulty():
    api = client()
    assert api.get("/api/v1/arena/levels/unknown/easy").status_code == 404
    response = api.get("/api/v1/arena/levels/hidden-mailbox/expert")
    assert response.status_code == 404
    assert "expert" in response.json()["detail"]


def test_levels_are_loaded_once():
    assert get_level_catalog() is get_level_catalog()


def test_arena_requires_a_session(monkeypatch):
    monkeypatch.setenv("ENV", "prod")
    get_settings.cache_clear()
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as api:
        assert api.get("/api/v1/arena/levels").status_code == 401


def test_every_arena_route_requires_a_session(monkeypatch):
    # The router-wide session dependency covers every route but the token-authenticated internal callback.
    import re

    from hackathon.main import app as main_app

    monkeypatch.setenv("ENV", "prod")
    get_settings.cache_clear()
    routes = [
        (method, re.sub(r"\{[^}]+\}", "x", route.path))
        for route in router.routes
        for method in route.methods
    ]
    assert len(routes) == 11
    api = TestClient(main_app)
    for method, path in routes:
        assert path.startswith("/api/v1/arena/")
        response = api.request(method, path, json={})
        assert response.status_code == 401, (method, path)


def test_main_app_serves_the_arena(monkeypatch):
    from hackathon.main import app

    app.dependency_overrides[require_user] = lambda: "github-user"
    try:
        with TestClient(app) as api:
            assert api.get("/api/v1/arena/levels").status_code == 200
    finally:
        app.dependency_overrides.clear()
