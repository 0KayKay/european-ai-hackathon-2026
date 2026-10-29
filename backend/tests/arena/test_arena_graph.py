"""AC-2 / AC-3: the level graph is built from the config."""

from functools import lru_cache

import pytest
import yaml

from hackathon.agent.challenge_1.prompts import challenge1_prompt
from hackathon.arena.config.loader import LEVELS_ROOT, load_level_document, load_levels
from hackathon.arena.graph import build_graph

ALL = [
    (level_id, difficulty)
    for level_id in (
        "hidden-mailbox",
        "email-review",
        "offer-review",
        "hidden-mailbox-output-judge",
    )
    for difficulty in ("easy", "medium", "hard")
]


@lru_cache
def levels():
    return load_levels()


def graph(level_id: str, difficulty: str):
    return build_graph(levels()[level_id].variants[difficulty])


def edge_kinds(g) -> set[str]:
    return {edge.kind for edge in g.edges}


@pytest.mark.parametrize("level_id,difficulty", ALL)
def test_graph_is_consistent(level_id, difficulty):
    g = graph(level_id, difficulty)
    ids = [node.id for node in g.nodes]
    assert len(ids) == len(set(ids))
    assert len({edge.id for edge in g.edges}) == len(g.edges)
    for edge in g.edges:
        assert edge.source in ids and edge.target in ids, edge.id
        assert edge.id == f"{edge.source}->{edge.target}"
    for node in g.nodes:
        if node.group:
            assert g.node(node.group).kind in {"guardrail_group", "tool_group"}
    for required in ("attacker", "agent", "tools", "success", "output"):
        assert required in ids
    assert g.model_dump(mode="json")


@pytest.mark.parametrize("difficulty", ["easy", "medium", "hard"])
def test_direct_multi_turn_level_has_reply_loop(difficulty):
    g = graph("hidden-mailbox", difficulty)
    assert g.surface.model_dump() == {
        "channel": "direct",
        "turns": "multi",
        "max_turns": 15,
    }
    [loop] = [edge for edge in g.edges if edge.kind == "reply_loop"]
    assert (loop.source, loop.target) == ("output", "attacker")
    assert "15" in loop.label
    assert g.node("injection") is None and g.node("user_task") is None
    first = "guardrails:user->agent" if difficulty == "hard" else "agent"
    assert any(
        e.source == "attacker" and e.target == first and e.kind == "attacker"
        for e in g.edges
    )


@pytest.mark.parametrize("level_id", ["email-review", "offer-review"])
@pytest.mark.parametrize("difficulty", ["easy", "medium", "hard"])
def test_indirect_levels_show_injection_and_no_reply_path(level_id, difficulty):
    g = graph(level_id, difficulty)
    assert g.surface.turns == "single"
    assert g.node("injection").kind == "injection"
    assert g.node("user_task").kind == "user_task"
    assert "reply_loop" not in edge_kinds(g)
    assert not [edge for edge in g.edges if edge.target == "attacker"]
    tool = levels()[level_id].variants[difficulty].surface.injection.tool
    assert {(e.source, e.target, e.kind) for e in g.edges} >= {
        ("attacker", "injection", "attacker"),
        ("injection", f"tool:{tool}", "attacker"),
    }


def test_guardrail_groups_follow_positions():
    g = graph("email-review", "hard")
    groups = {node.id: node for node in g.nodes if node.kind == "guardrail_group"}
    assert set(groups) == {
        "guardrails:user->agent",
        "guardrails:agent->output",
        "guardrails:around:send_email",
        "guardrails:tool->agent",
    }
    members = [node.id for node in g.nodes if node.group == "guardrails:agent->output"]
    assert members == ["guardrail:output-sanitizer", "guardrail:output-judge"]
    edges = {(e.source, e.target) for e in g.edges}
    assert ("agent", "guardrails:around:send_email") in edges
    assert ("guardrails:around:send_email", "tool:send_email") in edges
    assert ("agent", "tool:get_emails") in edges
    assert ("data:outbox", "success") in edges
    for difficulty in ("easy", "medium"):
        easy = graph("email-review", difficulty)
        assert not [node for node in easy.nodes if node.kind.startswith("guardrail")]
        assert ("agent", "tool:send_email") in {
            (e.source, e.target) for e in easy.edges
        }


def test_moving_a_guardrail_in_the_config_moves_it_in_the_graph():
    document = yaml.safe_load(
        (LEVELS_ROOT / "hidden-mailbox" / "level.yaml").read_text()
    )
    before = build_graph(
        load_level_document(document, LEVELS_ROOT / "hidden-mailbox").variants["hard"]
    )
    assert before.node("guardrail:input-judge").group == "guardrails:user->agent"

    [judge] = [
        g
        for g in document["difficulties"]["hard"]["guardrails"]
        if g["id"] == "input-judge"
    ]
    judge["at"] = "agent->output"
    after = build_graph(
        load_level_document(document, LEVELS_ROOT / "hidden-mailbox").variants["hard"]
    )

    node = after.node("guardrail:input-judge")
    assert node.group == "guardrails:agent->output"
    assert [n.id for n in after.nodes if n.group == "guardrails:user->agent"] == [
        "guardrail:input-sanitizer"
    ]
    # Order inside a position is the list order: the judge is listed before the output sanitizer.
    assert [n.id for n in after.nodes if n.group == "guardrails:agent->output"] == [
        "guardrail:input-judge",
        "guardrail:output-sanitizer",
    ]
    settings = {s.label: s.value for s in node.details.settings}
    assert (
        settings["Position"] == "agent->output"
        and settings["On block"] == "replace_output"
    )

    # Removing all input guardrails removes the group; the attacker then talks to the agent directly.
    document["difficulties"]["hard"]["guardrails"] = [
        g
        for g in document["difficulties"]["hard"]["guardrails"]
        if g["at"] != "user->agent"
    ]
    direct = build_graph(
        load_level_document(document, LEVELS_ROOT / "hidden-mailbox").variants["hard"]
    )
    assert direct.node("guardrails:user->agent") is None
    assert ("attacker", "agent") in {(e.source, e.target) for e in direct.edges}


def test_node_details_show_settings_full_prompts_and_models():
    level = levels()["hidden-mailbox"].variants["hard"]
    g = build_graph(level)
    agent = g.node("agent")
    assert agent.details.model == {
        "model": "glm-5.3-flash-leonardo",
        "endpoint": "agentgateway",
        "params": {"max_tokens": 4096, "extra": {"chat_template_kwargs": {"reasoning_effort": "low", "clear_thinking": True}}},
    }
    prompts = {prompt.name: prompt for prompt in agent.details.prompts}
    assert prompts["System prompt"].text == challenge1_prompt("User", "HARD")
    assert prompts["System prompt"].file == "hidden-mailbox/prompts/system.md"
    assert (
        prompts["Security rules (hard)"].file
        == "hidden-mailbox/prompts/security_hard.md"
    )

    judge = g.node("guardrail:input-judge")
    assert judge.details.model["params"] == {
        "temperature": 0,
        "max_tokens": 1024,
        "extra": {"chat_template_kwargs": {"reasoning_effort": "low", "clear_thinking": True}},
    }
    judge_prompts = {prompt.name: prompt for prompt in judge.details.prompts}
    assert "{{content}}" in judge_prompts["Policy"].text
    assert judge_prompts["Policy"].file == "hidden-mailbox/prompts/input_judge.md"
    assert judge_prompts["System prompt"].file == "shared/prompts/judge_system.md"
    assert judge_prompts["Refusal text"].text == level.refusal.text
    settings = {s.label: s.value for s in judge.details.settings}
    assert (
        settings["On block"] == "refuse"
        and settings["On timeout or failure"] == "block"
    )

    attacker = g.node("attacker")
    assert attacker.subtitle == "DeepTeam · 1 attack type · 5 cases"
    assert attacker.details.model["model"] == "glm-5.3-flash-leonardo"
    assert {p.name for p in attacker.details.prompts} == {
        "Purpose",
        "Success criteria",
        "Attacker knowledge",
    }
    data = g.node("data:drive")
    assert data.subtitle == "4 folders · 19 files"
    assert (
        data.details.prompts[0].text
        == (LEVELS_ROOT / "hidden-mailbox/data/drive.yaml").read_text()
    )


def test_model_roles_cover_every_llm_role():
    level = levels()["email-review"].variants["hard"]
    roles = {role["role"]: role for role in level.model_roles()}
    assert set(roles) == {
        "agent",
        "guardrail:output-judge",
        "guardrail:send-email-judge",
        "red_team.attacker",
        "red_team.simulator",
        "red_team.evaluator",
        "green_team.generator",
    }
    g = build_graph(level)
    for role in roles.values():
        assert role["node"] is None or g.node(role["node"]) is not None


def test_example_level_uses_an_output_judge_instead_of_an_input_judge():
    example = levels()["hidden-mailbox-output-judge"]
    level_1 = levels()["hidden-mailbox"]
    for difficulty in ("easy", "medium", "hard"):
        variant = example.variants[difficulty]
        assert (
            variant.agent.system_prompt
            == level_1.variants[difficulty].agent.system_prompt
        )
        assert (
            variant.agent.toolsets[0].data
            == level_1.variants[difficulty].agent.toolsets[0].data
        )
    hard = example.variants["hard"]
    judges = [(g.kind, g.at) for g in hard.guardrails if g.kind == "llm_judge"]
    assert judges == [("llm_judge", "agent->output")]
    g = build_graph(hard)
    assert g.node("guardrail:output-judge").group == "guardrails:agent->output"
    assert [n.id for n in g.nodes if n.group == "guardrails:user->agent"] == [
        "guardrail:input-sanitizer"
    ]
    assert "reply_loop" in edge_kinds(g)
    assert hard.guardrails_at("agent->output")[1].policy.runtime == ["content"]
    assert "hidden-mailbox-output-judge/prompts/output_judge.md" in hard.sources


def test_guardrail_labels_are_readable_and_configurable():
    g = graph("email-review", "hard")
    assert g.node("guardrail:send-email-judge").label == "Send email judge"
    assert g.node("guardrail:input-sanitizer").label == "Input sanitizer"
    # The implementation name stays visible in the inspector.
    judge = g.node("guardrail:send-email-judge")
    assert {s.label: s.value for s in judge.details.settings}["Name"] == (
        "SendEmailGuardrail"
    )

    doc = yaml.safe_load((LEVELS_ROOT / "email-review" / "level.yaml").read_text())
    output_judge = next(
        g
        for g in doc["difficulties"]["hard"]["guardrails"]
        if g["id"] == "output-judge"
    )
    output_judge["label"] = "Reply judge"
    level = load_level_document(doc, LEVELS_ROOT / "email-review")
    relabelled = build_graph(level.variants["hard"])
    assert relabelled.node("guardrail:output-judge").label == "Reply judge"


def test_model_parameters_are_shown_as_json_values():
    agent = graph("hidden-mailbox", "easy").node("agent")
    settings = {s.label: s.value for s in agent.details.settings}
    assert "max_tokens 4096" in settings["Model parameters"]
