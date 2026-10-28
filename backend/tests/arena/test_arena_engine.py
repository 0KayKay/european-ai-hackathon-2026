"""Engine behavior: conversations, limits, injection, outcomes, model roles, events.

No network: the agent is a `FunctionModel`, judges are fakes (or `GatewayJudge` over a fake OpenAI client).
Attacker-side inputs are neutral placeholders.
"""

import asyncio
import json
from datetime import datetime
from functools import lru_cache
from types import SimpleNamespace

import pytest
import structlog
import yaml
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import FunctionModel

from hackathon.arena.config.loader import LEVELS_ROOT, load_level_document, load_levels
from hackathon.arena.engine import (
    Case,
    CaseInput,
    CaseSession,
    EngineModels,
    GatewayJudge,
    ModelIdentity,
    gateway_agent_model,
    run_case,
)
from hackathon.arena.engine.guardrails import TOOL_RESULT_BLOCKED
from hackathon.arena.graph import build_graph
from hackathon.arena.models import agent_model_settings
from hackathon.arena.registry.guardrails import (
    JUDGE_UNAVAILABLE_REASON,
    JUDGE_UNAVAILABLE_RULE,
    GuardrailDecision,
)
from hackathon.config import get_settings

SECRET_FOLDER = "/Finanzen/Legacy/internal_only/"
SECRET_FILE = "prozess_notizen_legacy.txt"
EMAIL = {"subject": "<attack 1 subject>", "body": "<attack 1>"}
OFFER = {"additional_notes": "<attack 1>"}


@lru_cache
def levels():
    return load_levels()


def level(level_id: str, difficulty: str):
    return levels()[level_id].variants[difficulty]


def variant(level_id: str, difficulty: str, change):
    """Resolve an in-memory copy of a level file after `change(document)`."""
    document = yaml.safe_load((LEVELS_ROOT / level_id / "level.yaml").read_text())
    change(document)
    return load_level_document(document, LEVELS_ROOT / level_id).variants[difficulty]


def with_limits(resolved, **changes):
    limits = resolved.limits.model_copy(deep=True)
    for path, value in changes.items():
        section, name = path.split("__")
        setattr(getattr(limits, section), name, value)
    return resolved.model_copy(update={"limits": limits})


def safe(decision="SAFE"):
    async def judge(guardrail, prompt):
        return GuardrailDecision(decision=decision, rule="rule", reason="reason")

    return judge


def models(respond, judge=None):
    return EngineModels(
        agent=lambda spec, identity: FunctionModel(respond),
        judge=lambda identity: judge or safe(),
    )


def guardrail_events(result, final=True):
    return [
        e
        for e in result.events
        if e.kind == "guardrail" and (not final or e.data["status"] != "pending")
    ]


def user_prompts(messages):
    return [
        part.content
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, UserPromptPart)
    ]


# --- direct, multi-turn --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multi_turn_keeps_history_and_stops_when_success_is_met():
    seen = []
    resolved = level("hidden-mailbox", "easy")

    def respond(messages, info):
        seen.append(user_prompts(messages))
        if len(seen) == 1:
            return ModelResponse(parts=[TextPart("<reply 1>")])
        if len(seen) == 2:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "get_file_content",
                        {"folder_name": SECRET_FOLDER, "file_name": SECRET_FILE},
                    )
                ]
            )
        assert isinstance(messages[-1].parts[0], ToolReturnPart)
        return ModelResponse(parts=[TextPart("<reply 2> " + session.secret)])

    session = CaseSession(resolved, Case(team="red"), models=models(respond))
    first = await session.turn("<attack 1>")
    assert (first.reply, first.stop, first.success) == ("<reply 1>", False, False)
    second = await session.turn("<attack 2>")
    assert second.stop and second.success and session.secret in second.reply
    assert seen[1] == ["<attack 1>", "<attack 2>"]  # history kept across turns
    third = await session.turn("<attack 3>")
    assert third.stop and len(seen) == 3  # no further model call after success

    result = await session.finish()
    assert result.outcome == "succeeded"
    assert result.decided_by == {"kind": "success_check"}
    assert result.turns == 2
    assert result.success["met"] and result.success["kind"] == "secret_in_output"
    assert [e.kind for e in result.events if e.kind == "success_check"] == [
        "success_check"
    ] * 2
    assert [e.data["met"] for e in result.events if e.kind == "success_check"] == [
        False,
        True,
    ]
    # Same shape as `result.success`: {met, kind, detail}.
    check = next(e for e in result.events if e.kind == "success_check")
    assert set(check.data) == {"met", "kind", "detail"}
    assert check.data["kind"] == "secret_in_output"
    attacker = next(e for e in result.events if e.kind == "attacker_message")
    assert attacker.data == {"text": "<attack 1>", "team": "red"}
    assert result.usage.model_requests == 3 and result.usage.tool_calls == 1
    assert [e.turn for e in result.events if e.kind == "attacker_message"] == [1, 2]


@pytest.mark.asyncio
async def test_conversation_limits_count_across_turns():
    resolved = with_limits(
        level("hidden-mailbox", "easy"), conversation__model_requests=3
    )

    def respond(messages, info):
        if isinstance(messages[-1].parts[-1], UserPromptPart):
            return ModelResponse(parts=[ToolCallPart("list_folders", {})])
        return ModelResponse(parts=[TextPart("<reply>")])

    session = CaseSession(resolved, Case(team="red"), models=models(respond))
    assert not (await session.turn("<attack 1>")).stop  # 2 requests
    second = await session.turn("<attack 2>")  # 3rd request ok, 4th exceeds
    assert second.stop and second.limit == "conversation.model_requests"
    assert second.reply == ""
    assert not (await session.turn("<attack 3>")).reply
    result = await session.finish()
    assert result.outcome == "defended"
    assert result.decided_by == {
        "kind": "limit",
        "limit": "conversation.model_requests",
    }
    [limit] = [e for e in result.events if e.kind == "limit"]
    assert limit.turn == 2 and limit.data["limit"] == "conversation.model_requests"
    assert result.usage.model_requests == 3

    resolved = with_limits(level("hidden-mailbox", "easy"), conversation__tool_calls=1)
    session = CaseSession(resolved, Case(team="red"), models=models(respond))
    await session.turn("<attack 1>")
    second = await session.turn("<attack 2>")
    assert second.limit == "conversation.tool_calls"
    result = await session.finish()
    assert result.usage.tool_calls == 1


@pytest.mark.asyncio
async def test_run_limits_apply_per_turn():
    resolved = with_limits(level("hidden-mailbox", "easy"), run__tool_calls=2)
    session = CaseSession(
        resolved,
        Case(team="green"),
        models=models(
            lambda messages, info: ModelResponse(
                parts=[ToolCallPart("list_folders", {})]
            )
        ),
    )
    result = await session.turn("<message 1>")
    assert result.stop and result.limit == "run.tool_calls"
    case = await session.finish()
    assert case.outcome == "passed" and case.decided_by["kind"] == "limit"


@pytest.mark.asyncio
async def test_max_turns_ends_a_direct_case():
    def shorter(document):
        document["attack_surface"]["max_turns"] = 2

    resolved = variant("hidden-mailbox", "easy", shorter)
    session = CaseSession(
        resolved,
        Case(team="red"),
        models=models(lambda m, i: ModelResponse(parts=[TextPart("<reply>")])),
    )
    assert not (await session.turn("<attack 1>")).stop
    last = await session.turn("<attack 2>")
    assert last.stop and last.limit == "max_turns"
    result = await session.finish()
    assert result.outcome == "defended" and result.decided_by == {"kind": "turn_limit"}
    assert (
        result.events[-1].kind == "limit"
        and result.events[-1].data["limit"] == "max_turns"
    )


@pytest.mark.asyncio
async def test_run_timeout_is_an_error():
    resolved = level("hidden-mailbox", "easy")
    resolved = resolved.model_copy(
        update={"limits": resolved.limits.model_copy(update={"timeout_seconds": 0.05})}
    )

    async def slow(messages, info):
        await asyncio.sleep(10)

    session = CaseSession(resolved, Case(team="red"), models=models(slow))
    result = await session.turn("<attack 1>")
    assert result.stop and "time limit" in result.error
    case = await session.finish()
    assert case.outcome == "error" and case.decided_by == {"kind": "error"}
    assert case.error == result.error


# --- indirect, single-turn -----------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_indirect_content_lands_at_the_injection_point_with_the_user_task():
    resolved = level("email-review", "easy")
    seen = []

    def respond(messages, info):
        seen.append(messages)
        if len(seen) == 1:
            return ModelResponse(parts=[ToolCallPart("get_emails", {})])
        return ModelResponse(parts=[TextPart("<summary>")])

    session = CaseSession(
        resolved,
        Case(team="red", attack_type="prompt_injection"),
        user_email="sender@example.com",
        models=models(respond),
    )
    turn = await session.run_indirect(EMAIL)
    assert turn.stop and turn.reply == "<summary>" and not turn.success
    assert user_prompts(seen[0]) == [resolved.surface.user_task]
    inbox = seen[1][-1].parts[0].content
    assert inbox[1] == {"from": "sender@example.com", "kind": "email", **EMAIL}
    assert session.secret in inbox[0]["body"] and len(session.secret) == 32

    result = await session.finish()
    assert result.outcome == "defended" and result.decided_by == {"kind": "agent"}
    assert result.attack_type == "prompt_injection"
    kinds = [e.kind for e in result.events]
    assert kinds[:2] == ["injection", "user_task"]
    assert result.events[0].data == EMAIL  # exactly the injection fields
    assert result.events[1].data["text"] == resolved.surface.user_task
    assert kinds[-2:] == ["agent_reply", "success_check"]
    with pytest.raises(RuntimeError):
        await session.run_indirect(EMAIL)
    with pytest.raises(RuntimeError):
        await session.turn("<attack 1>")


@pytest.mark.asyncio
async def test_offer_injection_and_success():
    winning = '{"routing_target": "agent:ops.autoapprover", "price_fairness": "ok", "plausibility": "plausible"}'
    notes = []

    def respond(messages, info):
        last = messages[-1].parts[-1]
        if isinstance(last, UserPromptPart):
            return ModelResponse(
                parts=[ToolCallPart("parse_offer", {"requisition_id": "#1024-Z"})]
            )
        notes.append(last.content["additional_notes"])
        return ModelResponse(parts=[TextPart(winning)])

    result = await run_case(
        level("offer-review", "medium"),
        CaseInput(case=Case(team="red"), content=OFFER),
        models=models(respond),
    )
    assert notes == ["<attack 1>"]
    assert result.outcome == "succeeded"
    assert result.success["detail"]["checks"] == {
        "routing": True,
        "price": True,
        "plausibility": True,
    }


@pytest.mark.asyncio
async def test_invalid_injection_content_is_a_case_error():
    session = CaseSession(
        level("email-review", "easy"),
        Case(team="red"),
        models=models(lambda m, i: pytest.fail("no model call expected")),
    )
    turn = await session.run_indirect({"subject": "x" * 301, "body": ""})
    assert turn.stop and "exceeds 300" in turn.error
    result = await session.finish()
    assert result.outcome == "error" and result.events[-1].kind == "error"


# --- outcomes ----------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_green_case_blocked_by_a_guardrail_is_a_false_positive():
    respond = lambda m, i: ModelResponse(parts=[TextPart("<reply>")])  # noqa: E731
    blocked = await run_case(
        level("hidden-mailbox", "hard"),
        CaseInput(case=Case(team="green"), messages=["<benign 1>"]),
        models=models(respond, safe("UNSAFE")),
    )
    assert blocked.outcome == "false_positive"
    assert blocked.decided_by == {"kind": "guardrail", "guardrail_id": "input-judge"}

    passed = await run_case(
        level("hidden-mailbox", "hard"),
        CaseInput(case=Case(team="green"), messages=["<benign 1>"]),
        models=models(respond),
    )
    assert passed.outcome == "passed" and passed.decided_by == {"kind": "agent"}

    red = await run_case(
        level("email-review", "hard"),
        CaseInput(case=Case(team="red"), content=EMAIL),
        models=models(respond, safe("UNSAFE")),
    )
    assert red.outcome == "defended"
    assert red.decided_by == {"kind": "guardrail", "guardrail_id": "output-judge"}


@pytest.mark.asyncio
async def test_judge_error_or_timeout_blocks():
    async def broken(guardrail, prompt):
        raise RuntimeError("gateway down")

    async def slow(guardrail, prompt):
        await asyncio.sleep(10)

    resolved = level("hidden-mailbox", "hard")
    fast = resolved.model_copy(
        update={
            "guardrails": [
                g.model_copy(update={"timeout_seconds": 0.05})
                for g in resolved.guardrails
            ]
        }
    )
    for judge, target in [(broken, resolved), (slow, fast)]:
        session = CaseSession(
            target,
            Case(team="red"),
            models=models(
                lambda m, i: pytest.fail("the model must not be called"), judge
            ),
        )
        turn = await session.turn("<attack 1>")
        assert turn.reply == resolved.refusal.text and turn.blocked == ["input-judge"]
        result = await session.finish()
        final = guardrail_events(result)[-1]
        assert (final.data["status"], final.data["rule"], final.data["reason"]) == (
            "blocked",
            JUDGE_UNAVAILABLE_RULE,
            JUDGE_UNAVAILABLE_REASON,
        )
        assert final.data["failed"] is True
        assert result.usage.model_requests == 0


@pytest.mark.asyncio
async def test_on_error_allow_lets_the_action_pass():
    def allow(document):
        document["difficulties"]["hard"]["guardrails"][1]["on_error"] = "allow"

    async def broken(guardrail, prompt):
        raise RuntimeError("gateway down")

    session = CaseSession(
        variant("hidden-mailbox", "hard", allow),
        Case(team="green"),
        models=models(lambda m, i: ModelResponse(parts=[TextPart("<reply>")]), broken),
    )
    turn = await session.turn("<benign 1>")
    assert turn.reply == "<reply>" and not turn.blocked
    final = guardrail_events(await session.finish())[1]
    assert (final.data["guardrail_id"], final.data["status"]) == (
        "input-judge",
        "passed",
    )


@pytest.mark.asyncio
async def test_guardrail_decisions_are_recorded_exactly_pending_then_final():
    async def judge(guardrail, prompt):
        return GuardrailDecision(
            decision="UNSAFE",
            rule="Instruction override",
            reason="<reason as returned>",
        )

    result = await run_case(
        level("hidden-mailbox", "hard"),
        CaseInput(case=Case(team="red"), messages=["<attack 1>"]),
        models=models(lambda m, i: pytest.fail("not called"), judge),
    )
    checks = [
        (e.data["guardrail_id"], e.data["status"], e.data["rule"], e.data["reason"])
        for e in guardrail_events(result, final=False)
    ]
    assert checks == [
        (
            "input-sanitizer",
            "sanitized",
            "Character normalization",
            "Text checked; no character changes needed.",
        ),
        ("input-judge", "pending", "", "Evaluating the security policy."),
        ("input-judge", "blocked", "Instruction override", "<reason as returned>"),
    ]
    pending, final = guardrail_events(result, final=False)[1:]
    assert pending.data["check_id"] == final.data["check_id"]
    assert (
        final.data["position"] == "user->agent"
        and final.data["model"] == "glm-5.3-flash-leonardo"
    )
    assert [e.kind for e in result.events][-1] == "success_check"


# --- guardrail placement from config ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_moving_a_guardrail_changes_where_it_runs():
    def move(document):
        document["difficulties"]["hard"]["guardrails"][1]["at"] = "agent->output"

    moved = variant("hidden-mailbox", "hard", move)
    prompts = []

    async def judge(guardrail, prompt):
        prompts.append((guardrail.id, prompt))
        return GuardrailDecision(decision="UNSAFE", rule="rule", reason="reason")

    seen = []

    def respond(messages, info):
        seen.append(messages)
        return ModelResponse(parts=[TextPart("<model reply 1>")])

    session = CaseSession(moved, Case(team="red"), models=models(respond, judge))
    turn = await session.turn("<attack 1>")
    assert len(seen) == 1  # the model is called: no input judge any more
    [(guardrail_id, prompt)] = prompts
    assert guardrail_id == "input-judge"
    assert "<model reply 1>" in prompt and "<attack 1>" not in prompt
    assert turn.reply == moved.refusal.text
    result = await session.finish()
    final = guardrail_events(result)
    # Config order inside agent->output: the moved judge, then the output sanitizer (skipped after a block).
    assert [(e.data["guardrail_id"], e.data["position"]) for e in final] == [
        ("input-sanitizer", "user->agent"),
        ("input-judge", "agent->output"),
    ]
    graph = build_graph(moved)
    assert graph.node("guardrail:input-judge").group == "guardrails:agent->output"
    assert_events_on_graph(result.events, moved)


@pytest.mark.asyncio
async def test_a_judge_on_tool_results_replaces_the_blocked_result():
    def move(document):
        guardrails = document["difficulties"]["hard"]["guardrails"]
        judge = next(g for g in guardrails if g["id"] == "output-judge")
        judge["at"] = "tool->agent"

    resolved = variant("email-review", "hard", move)
    returned = []

    def respond(messages, info):
        last = messages[-1].parts[-1]
        if isinstance(last, UserPromptPart):
            return ModelResponse(parts=[ToolCallPart("get_emails", {})])
        returned.append(last.content)
        return ModelResponse(parts=[TextPart("<reply>")])

    prompts = []

    async def judge(guardrail, prompt):
        prompts.append(prompt)
        return GuardrailDecision(decision="UNSAFE", rule="rule", reason="reason")

    session = CaseSession(resolved, Case(team="red"), models=models(respond, judge))
    await session.run_indirect(EMAIL)
    assert returned == [TOOL_RESULT_BLOCKED]
    assert "<attack 1 subject>" in prompts[0]
    result = await session.finish()
    [tool_result] = [e for e in result.events if e.kind == "tool_result"]
    assert tool_result.data["blocked_by"] == "output-judge"
    assert result.decided_by == {"kind": "guardrail", "guardrail_id": "output-judge"}


# --- model roles (AC-9) ------------------------------------------------------------------------------------------


class FakeOpenAI:
    """Captures OpenAI client options and chat-completion requests of the judges."""

    calls: list[dict] = []

    def __init__(self, **options):
        self.options = options
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    async def create(self, **request):
        self.calls.append({"options": self.options, "request": request})
        content = json.dumps({"decision": "SAFE", "rule": "r", "reason": "x"})
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None


def email_script(messages, info):
    last = messages[-1].parts[-1]
    if isinstance(last, UserPromptPart):
        return ModelResponse(parts=[ToolCallPart("get_emails", {})])
    if last.tool_name == "get_emails":
        return ModelResponse(
            parts=[
                ToolCallPart("send_email", {"to": "x@example.com", "body": "<body 1>"})
            ]
        )
    return ModelResponse(parts=[TextPart("<reply>")])


async def judge_requests(resolved) -> tuple[dict, list[dict]]:
    FakeOpenAI.calls = []
    agents = []

    def agent(spec, identity):
        agents.append(spec)
        return FunctionModel(email_script)

    result = await run_case(
        resolved,
        CaseInput(case=Case(id="case-9", team="red"), content=EMAIL),
        user_id="user-9",
        models=EngineModels(
            agent=agent,
            judge=lambda identity: GatewayJudge(identity, client_factory=FakeOpenAI),
        ),
    )
    by_role = {}
    for call, guardrail_id in zip(
        FakeOpenAI.calls, ["send-email-judge", "output-judge"], strict=True
    ):
        by_role[guardrail_id] = call
    return {"agent": agents, "judges": by_role, "result": result}, FakeOpenAI.calls


@pytest.mark.asyncio
async def test_each_role_gets_its_own_model_parameters():
    base, _ = await judge_requests(level("email-review", "hard"))
    send, output = base["judges"]["send-email-judge"], base["judges"]["output-judge"]
    assert send["request"]["temperature"] == output["request"]["temperature"] == 0
    assert send["options"]["default_headers"]["x-model"] == "glm-5.3-flash-leonardo"
    assert send["options"]["default_headers"]["x-thread-id"] == "case-9"
    assert send["options"]["default_headers"]["x-user-id"] == "user-9"
    assert send["options"]["max_retries"] == 0 and send["options"]["timeout"] == 30

    def tune_send_judge(document):
        guardrails = document["difficulties"]["hard"]["guardrails"]
        judge = next(g for g in guardrails if g["id"] == "send-email-judge")
        judge["model"] = {
            "model": "judge-model-b",
            "params": {"temperature": 0.5, "presence_penalty": 0.25},
        }

    tuned, _ = await judge_requests(variant("email-review", "hard", tune_send_judge))
    send_b, output_b = (
        tuned["judges"]["send-email-judge"],
        tuned["judges"]["output-judge"],
    )
    assert send_b["request"]["model"] == "judge-model-b"
    assert send_b["request"]["temperature"] == 0.5
    assert send_b["request"]["presence_penalty"] == 0.25
    assert send_b["options"]["default_headers"]["x-model"] == "judge-model-b"
    assert output_b == output  # the other judge is unchanged
    assert tuned["agent"] == base["agent"]  # and so is the agent
    roles = {r["role"]: r for r in tuned["result"].models}
    assert roles["guardrail:send-email-judge"]["params"] == {
        "temperature": 0.5,
        "presence_penalty": 0.25,
    }
    assert roles["guardrail:output-judge"]["params"]["temperature"] == 0

    def tune_agent(document):
        document["agent"]["model"]["params"]["temperature"] = 0.3

    agent_tuned, _ = await judge_requests(variant("email-review", "hard", tune_agent))
    [spec] = agent_tuned["agent"]
    assert spec.params.temperature == 0.3
    assert agent_tuned["judges"] == base["judges"]
    assert {r["role"]: r for r in agent_tuned["result"].models}["agent"]["params"][
        "temperature"
    ] == 0.3


@pytest.mark.asyncio
async def test_gateway_key_is_only_sent_to_agentgateway():
    def explicit_endpoint(document):
        guardrails = document["difficulties"]["hard"]["guardrails"]
        judge = next(g for g in guardrails if g["id"] == "send-email-judge")
        judge["model"] = {"model": "judge-b", "endpoint": "https://example.com/v1"}

    tuned, _ = await judge_requests(
        variant("email-review", "hard", explicit_endpoint)
    )
    send = tuned["judges"]["send-email-judge"]["options"]
    output = tuned["judges"]["output-judge"]["options"]
    assert send["base_url"] == "https://example.com/v1"
    assert send["api_key"] != get_settings().AGENTGATEWAY_API_KEY
    assert output["api_key"] == get_settings().AGENTGATEWAY_API_KEY


def test_gateway_agent_model_uses_the_role_spec_and_headers():
    spec = level("hidden-mailbox", "easy").agent.model
    tuned = spec.model_copy(
        update={"params": spec.params.model_copy(update={"temperature": 0.4})}
    )
    structlog.contextvars.bind_contextvars(correlation_id="corr-1")
    try:
        model = gateway_agent_model(tuned, ModelIdentity(user_id="u", case_id="c"))
    finally:
        structlog.contextvars.unbind_contextvars("correlation_id")
    assert model.model_name == "glm-5.3-flash-leonardo"
    assert model.settings == agent_model_settings(tuned)
    assert model.settings["temperature"] == 0.4
    client = model.client
    assert str(client.base_url).rstrip("/") == "http://localhost:4000/v1"
    headers = client.default_headers
    assert headers["x-model"] == "glm-5.3-flash-leonardo"
    assert headers["x-user-id"] == "u" and headers["x-correlation-id"] == "corr-1"


@pytest.mark.asyncio
async def test_events_never_contain_headers_or_credentials(monkeypatch):
    key = "sk-arena-credential-4711"
    monkeypatch.setattr(get_settings(), "AGENTGATEWAY_API_KEY", key)
    structlog.contextvars.bind_contextvars(correlation_id="corr-secret-42")
    try:
        details, calls = await judge_requests(level("email-review", "hard"))
    finally:
        structlog.contextvars.unbind_contextvars("correlation_id")
    assert calls and all(call["options"]["api_key"] == key for call in calls)
    assert (
        calls[0]["options"]["default_headers"]["x-correlation-id"] == "corr-secret-42"
    )
    dumped = details["result"].model_dump_json()
    for value in [key, "corr-secret-42", "x-user-id", "x-correlation-id", "x-model"]:
        assert value not in dumped


# --- events -------------------------------------------------------------------------------------------------


def assert_events_on_graph(events, resolved):
    graph = build_graph(resolved)
    nodes = {node.id for node in graph.nodes}
    edges = {edge.id for edge in graph.edges}
    for event in events:
        assert event.node is None or event.node in nodes, (event.kind, event.node)
        assert event.edge is None or event.edge in edges, (event.kind, event.edge)


def generic_script(resolved):
    tools = [tool.name for tool in resolved.tools]

    def respond(messages, info):
        calls = sum(
            isinstance(part, ToolCallPart)
            for message in messages
            if isinstance(message, ModelResponse)
            for part in message.parts
        )
        if calls < len(tools):
            name = tools[calls]
            args = {
                "get_file_content": {"folder_name": "/x/", "file_name": "y"},
                "list_files_of_folder": {"folder_name": "/x/"},
                "send_email": {"to": "x@example.com", "body": "<b>"},
                "get_price_reference": {"sku": "QPU-CC-512-A1"},
                "parse_offer": {"requisition_id": "#1024-Z"},
            }.get(name, {})
            return ModelResponse(parts=[ToolCallPart(name, args)])
        return ModelResponse(parts=[TextPart("<reply>")])

    return respond


@pytest.mark.parametrize(
    "level_id,difficulty",
    [
        (level_id, difficulty)
        for level_id in [
            "hidden-mailbox",
            "email-review",
            "offer-review",
            "hidden-mailbox-output-judge",
        ]
        for difficulty in ("easy", "medium", "hard")
    ],
)
@pytest.mark.asyncio
async def test_events_follow_the_schema_and_reference_the_level_graph(
    level_id, difficulty
):
    resolved = level(level_id, difficulty)
    live = []

    async def listener(event):
        live.append(event)

    case = Case(team="red")
    case_input = (
        CaseInput(case=case, content=OFFER if level_id == "offer-review" else EMAIL)
        if resolved.surface.channel == "indirect"
        else CaseInput(case=case, messages=["<attack 1>", "<attack 2>"])
    )
    result = await run_case(
        resolved,
        case_input,
        models=models(generic_script(resolved)),
        listener=listener,
    )
    events = result.events
    assert live == events
    assert [e.seq for e in events] == list(range(len(events)))
    assert all(a.t <= b.t for a, b in zip(events, events[1:]))
    for event in events:
        assert datetime.fromisoformat(event.at.replace("Z", "+00:00"))
        assert event.at.endswith("Z")
    assert_events_on_graph(events, resolved)
    kinds = {e.kind for e in events}
    assert {
        "model_call",
        "tool_call",
        "tool_result",
        "agent_reply",
        "success_check",
    } <= kinds
    if resolved.surface.channel == "direct":
        assert "attacker_message" in kinds and result.turns == 2
    else:
        assert {"injection", "user_task"} <= kinds
    assert {e.node for e in events if e.kind == "tool_call"} == {
        f"tool:{tool.name}" for tool in resolved.tools
    }
    if resolved.guardrails:
        guardrail_nodes = {e.node for e in events if e.kind == "guardrail"}
        assert guardrail_nodes <= {f"guardrail:{g.id}" for g in resolved.guardrails}
        assert guardrail_nodes
    json.dumps([e.model_dump() for e in events])  # serializable for persistence


@pytest.mark.asyncio
async def test_a_failing_listener_does_not_change_the_case():
    async def listener(event):
        raise RuntimeError("live view gone")

    result = await run_case(
        level("hidden-mailbox", "easy"),
        CaseInput(case=Case(team="green"), messages=["<message 1>"]),
        models=models(lambda m, i: ModelResponse(parts=[TextPart("<reply>")])),
        listener=listener,
    )
    assert result.outcome == "passed" and result.events


@pytest.mark.asyncio
async def test_driver_errors_and_unstarted_cases_are_errors():
    session = CaseSession(
        level("hidden-mailbox", "easy"),
        Case(team="red"),
        models=models(lambda m, i: ModelResponse(parts=[TextPart("<reply>")])),
    )
    await session.turn("<attack 1>")
    result = await session.finish(error="Attacker failed.")
    assert result.outcome == "error" and result.error == "Attacker failed."
    assert await session.finish() is result

    unstarted = await CaseSession(
        level("hidden-mailbox", "easy"), Case(team="red"), models=models(None)
    ).finish()
    assert unstarted.outcome == "error"
