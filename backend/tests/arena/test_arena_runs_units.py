"""Units of the run package: DeepTeam client, green generator, results, store helpers. No network."""

import json
from functools import lru_cache
from types import SimpleNamespace

import httpx
import pytest

from hackathon.arena.config.loader import load_levels
from hackathon.arena.runs import results
from hackathon.arena.runs.green_team import (
    GreenGenerator,
    GreenTeamError,
    generator_request,
)
from hackathon.arena.runs.red_team import (
    DeepTeamClient,
    RedTeamError,
    TurnCallback,
    check_generated,
)
from hackathon.config import get_settings
from hackathon.arena.runs.store import (
    MemoryRunStore,
    PostgresRunStore,
    case_order,
    create_store,
    dataset_meta,
    decode_case,
    encode_case,
    interrupt,
)


@lru_cache
def levels():
    return load_levels()


def level(level_id: str, difficulty: str = "hard"):
    return levels()[level_id].variants[difficulty]


def ndjson(*lines) -> bytes:
    return "".join(json.dumps(line) + "\n" for line in lines).encode()


# --- DeepTeam client -------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deepteam_generate_speaks_the_ndjson_contract():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            content=ndjson(
                {
                    "type": "case",
                    "case": {
                        "attack_type": "prompt_injection",
                        "content": {"subject": "<subject 1>", "body": "<body 1>"},
                    },
                },
                {"type": "done", "count": 1},
                {"type": "case", "case": {"attack_type": "ignored after done"}},
            ),
        )

    client = DeepTeamClient(
        "http://deepteam:8080/", "gateway-key", transport=httpx.MockTransport(handler)
    )
    resolved = level("email-review")
    cases = [
        case
        async for case in client.generate(
            resolved, run_id="run-1", attacks=[("prompt_injection", 1)]
        )
    ]
    assert cases == [
        {
            "attack_type": "prompt_injection",
            "content": {"subject": "<subject 1>", "body": "<body 1>"},
        }
    ]
    assert seen["path"] == "/internal/arena/generate"
    assert seen["auth"] == "Bearer gateway-key"
    body = seen["body"]
    assert body["run_id"] == "run-1" and body["level_id"] == "email-review"
    assert body["attacks"] == [{"type": "prompt_injection", "count": 1}]
    assert body["surface"] == {
        "channel": "indirect",
        "injection": {
            "point": "tool_output",
            "description": "One inbox email, delivered as the 2nd email by get_emails.",
            "fields": [
                {"name": "subject", "max_length": 300},
                {"name": "body", "max_length": 10000},
            ],
        },
    }
    assert set(body["objective"]) == {"purpose", "criteria", "attacker_knowledge"}
    # `endpoint: agentgateway` is resolved to the gateway base URL; no credentials in the body.
    assert body["models"]["attacker"] == {
        "model": "glm-5.3-flash-leonardo",
        "endpoint": "http://localhost:4000/v1",
        "params": {"temperature": 0.7, "max_tokens": 512, "extra": {"chat_template_kwargs": {"reasoning_effort": "low", "clear_thinking": True}}},
    }
    assert "gateway-key" not in json.dumps(body)


@pytest.mark.asyncio
async def test_deepteam_errors():
    async def collect(handler):
        client = DeepTeamClient(
            "http://deepteam:8080", "k", transport=httpx.MockTransport(handler)
        )
        return [
            case
            async for case in client.generate(
                level("email-review"), run_id="r", attacks=[("roleplay", 1)]
            )
        ]

    cases = [
        (
            lambda r: httpx.Response(
                200, content=ndjson({"type": "error", "message": "boom"})
            ),
            "boom",
        ),
        (
            lambda r: httpx.Response(200, content=ndjson({"type": "case", "case": {}})),
            "without 'done'",
        ),
        (lambda r: httpx.Response(401), "answered 401"),
        (lambda r: httpx.Response(200, content=b"not json\n"), "invalid stream line"),
    ]
    for handler, message in cases:
        with pytest.raises(RedTeamError, match=message):
            await collect(handler)

    def refuse(request):
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(RedTeamError, match="unreachable at http://deepteam:8080"):
        await collect(refuse)


@pytest.mark.asyncio
async def test_deepteam_multi_turn_request():
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            content=ndjson(
                {
                    "type": "turn",
                    "turn": 1,
                    "attacker_message": "<attack 1>",
                    "reply": "<reply>",
                },
                {"type": "done", "turns": 1},
            ),
        )

    async def call(message):
        raise AssertionError("the remote driver uses the URL, not the in-process call")

    client = DeepTeamClient(
        "http://deepteam:8080", "k", transport=httpx.MockTransport(handler)
    )
    await client.drive(
        level("hidden-mailbox"),
        case_id="case-1",
        attack_type="linear_jailbreaking",
        max_turns=15,
        callback=TurnCallback(url="http://backend:8000/cb", token="tok", call=call),
    )
    assert seen["path"] == "/internal/arena/multi-turn"
    body = seen["body"]
    assert (body["case_id"], body["attack_type"], body["max_turns"]) == (
        "case-1",
        "linear_jailbreaking",
        15,
    )
    assert body["callback"] == {"url": "http://backend:8000/cb", "token": "tok"}
    assert set(body["models"]) == {"attacker", "simulator", "evaluator"}

    def failing(request):
        return httpx.Response(200, content=ndjson({"type": "error"}))

    client = DeepTeamClient(
        "http://deepteam:8080", "k", transport=httpx.MockTransport(failing)
    )
    with pytest.raises(RedTeamError, match="unknown error"):
        await client.drive(
            level("hidden-mailbox"),
            case_id="c",
            attack_type="linear_jailbreaking",
            max_turns=2,
            callback=TurnCallback(url="u", token="t", call=call),
        )


def test_check_generated():
    direct, indirect = level("hidden-mailbox"), level("email-review")
    assert check_generated(
        direct, {"attack_type": "roleplay", "input": "<attack 1>"}
    ) == (
        {"attack_type": "roleplay", "input": "<attack 1>"},
        None,
    )
    assert check_generated(direct, {"attack_type": "roleplay", "input": " "})[1]
    assert check_generated(direct, "x")[1]
    ok = {"subject": "<subject 1>", "body": "<body 1>"}
    assert (
        check_generated(indirect, {"attack_type": "roleplay", "content": ok})[1] is None
    )
    for content, problem in [
        ({"subject": "<s>"}, "missing injection field(s): body"),
        ({**ok, "to": "<x>"}, "unknown injection field(s): to"),
        ({**ok, "subject": "x" * 301}, "exceeds 300 characters"),
        ({**ok, "body": 3}, "must be a string"),
    ]:
        assert problem in check_generated(indirect, {"content": content})[1]
    assert check_generated(indirect, {"input": "<attack 1>"})[1]


# --- green generator ---------------------------------------------------------------------------------------


class FakeOpenAI:
    """Async context manager like `AsyncOpenAI`; returns the queued replies."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []
        self.options = None
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def __call__(self, **options):
        self.options = options
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def create(self, **request):
        self.requests.append(request)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=reply))]
        )


def reply(*cases) -> str:
    return json.dumps({"cases": list(cases)})


@pytest.mark.asyncio
async def test_green_generator_direct_level():
    fake = FakeOpenAI(
        [
            reply({"input": "<message 1>"}, {"input": ""}, {"input": "<message 2>"}),
            "```json\n" + reply({"input": "<message 3>"}) + "\n```",
        ]
    )
    generator = GreenGenerator(fake, batch_size=3)
    resolved = level("hidden-mailbox")
    cases = [
        case
        async for case in generator.generate(
            resolved, count=3, user_id="tester", run_id="run-1"
        )
    ]
    assert cases == [{"input": f"<message {n}>"} for n in (1, 2, 3)]
    assert fake.options["base_url"] == "http://localhost:4000/v1"
    assert fake.options["api_key"] == get_settings().AGENTGATEWAY_API_KEY
    assert fake.options["default_headers"]["x-model"] == "glm-5.3-flash-leonardo"
    assert fake.options["default_headers"]["x-user-id"] == "tester"
    first = fake.requests[0]
    assert first["temperature"] == 0.9 and first["model"] == "glm-5.3-flash-leonardo"
    assert first["max_completion_tokens"] == 1024
    assert first["extra_body"] == {"chat_template_kwargs": {"reasoning_effort": "low", "clear_thinking": True}}
    schema = first["response_format"]["json_schema"]["schema"]
    assert schema["properties"]["cases"]["items"]["required"] == ["input"]
    system = first["messages"][0]["content"]
    assert resolved.green_team.guidance.text in system
    assert "Generate 3 new, distinct cases." == first["messages"][1]["content"]
    assert fake.requests[1]["messages"][1]["content"].startswith("Generate 1 new")


@pytest.mark.asyncio
async def test_green_generator_indirect_level_respects_field_limits():
    resolved = level("email-review")
    fake = FakeOpenAI(
        [
            reply(
                {"subject": "<benign subject 1>", "body": "<benign body 1>"},
                {"subject": "x" * 301, "body": "<benign body 2>"},
            ),
            reply({"subject": "<benign subject 3>", "body": "<benign body 3>"}),
        ]
    )
    cases = [
        case
        async for case in GreenGenerator(fake, batch_size=2).generate(
            resolved, count=2, user_id="u", run_id="r"
        )
    ]
    assert [case["content"]["subject"] for case in cases] == [
        "<benign subject 1>",
        "<benign subject 3>",
    ]
    request = fake.requests[0]
    assert request["response_format"]["json_schema"]["schema"]["properties"]["cases"][
        "items"
    ]["required"] == ["subject", "body"]
    system = request["messages"][0]["content"]
    assert "Please process my email inbox." in system
    assert "`subject` (at most 300 characters)" in system


@pytest.mark.asyncio
async def test_green_generator_failures():
    resolved = level("hidden-mailbox")
    generator = GreenGenerator(FakeOpenAI([ConnectionError("down")]))
    with pytest.raises(GreenTeamError, match="ConnectionError"):
        async for _ in generator.generate(resolved, count=2, user_id="u", run_id="r"):
            pass
    generator = GreenGenerator(FakeOpenAI(["{}", "nope", reply()]), batch_size=2)
    with pytest.raises(GreenTeamError, match="produced 0 of 2"):
        async for _ in generator.generate(resolved, count=2, user_id="u", run_id="r"):
            pass
    assert generator_request(resolved, 2, 0)["messages"][1]["content"].startswith(
        "Generate 2"
    )


# --- results -------------------------------------------------------------------------------------------------


def snapshot_run(level_id="hidden-mailbox", difficulty="hard"):
    from hackathon.arena.graph import build_graph

    resolved = level(level_id, difficulty)
    return {
        "plan": {"red": 4, "green": 2},
        "snapshot": {
            "level": resolved.model_dump(mode="json"),
            "graph": build_graph(resolved).model_dump(mode="json"),
        },
    }


def test_results_rates_and_labels():
    run = snapshot_run()
    # Exercise configured-but-unused attack rows alongside observed cases.
    run["snapshot"]["level"]["red_team"]["attacks"] = [
        {"type": "prompt_injection", "attempts": 5},
        {"type": "roleplay", "attempts": 5},
        {"type": "linear_jailbreaking", "attempts": 5},
    ]
    cases = [
        {
            "id": "a",
            "team": "red",
            "index": 1,
            "attack_type": "roleplay",
            "outcome": "succeeded",
            "decided_by": {"kind": "success_check"},
        },
        {
            "id": "b",
            "team": "red",
            "index": 2,
            "attack_type": "roleplay",
            "outcome": "defended",
            "decided_by": {"kind": "limit", "limit": "run.tool_calls"},
        },
        {
            "id": "c",
            "team": "red",
            "index": 3,
            "attack_type": "linear_jailbreaking",
            "outcome": "defended",
            "decided_by": {"kind": "turn_limit"},
        },
        {
            "id": "d",
            "team": "red",
            "index": 4,
            "attack_type": "roleplay",
            "outcome": "error",
            "decided_by": {"kind": "error"},
        },
        {
            "id": "e",
            "team": "green",
            "index": 1,
            "outcome": "passed",
            "decided_by": {"kind": "agent"},
            "content": None,
            "input": "x" * 300,
        },
        {"id": "f", "team": "green", "index": 2, "outcome": None, "decided_by": None},
    ]
    view = results.results(run, cases)
    assert view["rates"]["attack_success"] == {
        "value": 1 / 3,
        "numerator": 1,
        "denominator": 3,
    }
    assert view["rates"]["false_positive"] == {
        "value": 0.0,
        "numerator": 0,
        "denominator": 1,
    }
    assert view["errors"] == {"red": 1, "green": 0}
    assert [
        (row["label"], row["red"], row["green"]) for row in view["by_decision"]
    ] == [
        ("Input judge · UNSAFE", 0, 0),
        ("Passed all guardrails · success check not met", 0, 1),
        ("Success check · secret in agent output", 1, 0),
        ("Run limit reached · 10 tool calls", 1, 0),
        ("Turn limit reached · 15 turns", 1, 0),
        ("Error", 1, 0),
    ]
    assert [row["type"] for row in view["by_attack_type"]] == [
        "prompt_injection",
        "roleplay",
        "linear_jailbreaking",
    ]
    assert view["by_attack_type"][0]["attempts"] == 0
    summaries = {case["id"]: case for case in view["cases"]}
    assert (
        summaries["e"]["label"] == "G01" and len(summaries["e"]["input_preview"]) == 160
    )
    assert summaries["f"]["outcome"] is None and summaries["f"]["decided_label"] is None
    assert results.progress(run, cases) == {
        "red": {"done": 4, "total": 4},
        "green": {"done": 1, "total": 2},
    }


def test_input_preview_follows_the_injection_fields_not_the_stored_key_order():
    # Postgres JSONB returns object keys shortest first ({body, subject}), the live run has field order.
    run = snapshot_run("email-review")
    stored = {"body": "<body 1>", "subject": "<subject 1>"}
    case = {"id": "a", "team": "red", "index": 1, "content": stored}
    assert results.case_summary(run, case)["input_preview"] == (
        "<subject 1> · <body 1>"
    )


# --- store ---------------------------------------------------------------------------------------------------


def test_store_helpers():
    run = {"id": "r", "status": "running", "finished_at": None}
    assert interrupt(run, "2026-10-27T00:00:00.000Z") == {
        "id": "r",
        "status": "interrupted",
        "finished_at": "2026-10-27T00:00:00.000Z",
    }
    case = {"id": "c", "run_id": "r", "team": "red", "index": 1, "events": [{"seq": 0}]}
    data, events = encode_case(case)
    assert "events" not in data and events == [{"seq": 0}]
    assert decode_case(json.dumps(data), json.dumps(events)) == case
    assert decode_case(json.dumps(data)) == data
    assert dataset_meta({"id": "d", "cases": []}) == {"id": "d"}
    assert sorted(
        [
            {"team": "green", "index": 1},
            {"team": "red", "index": 2},
            {"team": "red", "index": 1},
        ],
        key=case_order,
    ) == [
        {"team": "red", "index": 1},
        {"team": "red", "index": 2},
        {"team": "green", "index": 1},
    ]
    assert isinstance(create_store("memory"), MemoryRunStore)
    assert isinstance(create_store("postgres"), PostgresRunStore)
    assert isinstance(create_store(), PostgresRunStore)  # default
    with pytest.raises(ValueError):
        create_store("sqlite")


@pytest.mark.asyncio
async def test_memory_store_copies_and_orders():
    store = MemoryRunStore()
    run = {"id": "r", "level": "l", "status": "completed", "started_at": "1"}
    await store.save_run(run)
    run["status"] = "changed"
    assert (await store.get_run("r"))["status"] == "completed"
    await store.save_case(
        {"id": "g", "run_id": "r", "team": "green", "index": 1, "events": [1]}
    )
    await store.save_case(
        {"id": "x", "run_id": "r", "team": "red", "index": 1, "events": [2]}
    )
    assert [c["id"] for c in await store.list_cases("r")] == ["x", "g"]
    assert "events" not in (await store.list_cases("r"))[0]
    assert (await store.list_cases("r", events=True))[0]["events"] == [2]
    assert await store.get_case("other", "x") is None
    await store.save_dataset(
        {"id": "d1", "level": "l", "created_at": "1", "cases": [1]}
    )
    await store.save_dataset(
        {"id": "d2", "level": "l", "created_at": "2", "cases": [1]}
    )
    assert [d["id"] for d in await store.list_datasets("l")] == ["d2", "d1"]
    assert await store.list_datasets("other") == []
