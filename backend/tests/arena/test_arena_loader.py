"""R-CFG-5: invalid level configs are rejected with an error naming the file and the problem."""

import shutil

import pytest
import yaml

from hackathon.arena.config.loader import (
    LEVELS_ROOT,
    LevelConfigError,
    deep_merge,
    load_level_document,
    load_levels,
)
from hackathon.arena.config.resolved import InjectionError
from hackathon.arena.config.templates import (
    TemplateError,
    placeholders,
    render,
    render_partial,
)


def document(level_id: str) -> dict:
    return yaml.safe_load((LEVELS_ROOT / level_id / "level.yaml").read_text())


def error(doc: dict, level_id: str) -> str:
    with pytest.raises(LevelConfigError) as exc:
        load_level_document(doc, LEVELS_ROOT / level_id)
    return str(exc.value)


def hard_guardrail(doc: dict, guardrail_id: str) -> dict:
    return next(
        g for g in doc["difficulties"]["hard"]["guardrails"] if g["id"] == guardrail_id
    )


@pytest.fixture
def levels_copy(tmp_path):
    root = tmp_path / "levels"
    shutil.copytree(LEVELS_ROOT, root)
    return root


def test_unknown_tool_in_around_position():
    doc = document("email-review")
    hard_guardrail(doc, "send-email-judge")["at"] = "around:send_mail"
    assert error(doc, "email-review") == (
        "levels/email-review/level.yaml: [hard] guardrails[3].at: unknown tool 'send_mail' in around:send_mail"
    )


def test_unknown_position_and_wrong_kind_for_position():
    doc = document("hidden-mailbox")
    hard_guardrail(doc, "input-judge")["at"] = "agent->user"
    assert "[hard] guardrails[1].at: unknown position 'agent->user'" in error(
        doc, "hidden-mailbox"
    )
    doc = document("hidden-mailbox")
    hard_guardrail(doc, "input-judge")["at"] = "around:get_file_content"
    message = error(doc, "hidden-mailbox")
    assert (
        "guardrails[1].at: guardrail kind 'llm_judge' cannot run at around:get_file_content"
        in message
    )


def test_on_block_must_fit_the_position():
    doc = document("hidden-mailbox")
    hard_guardrail(doc, "input-judge")["on_block"] = "replace_output"
    assert "[hard] guardrails[1].on_block: must be 'refuse' at user->agent" in error(
        doc, "hidden-mailbox"
    )


def test_judge_needs_a_policy_and_a_sanitizer_takes_none():
    doc = document("hidden-mailbox")
    del hard_guardrail(doc, "input-judge")["policy"]
    assert "guardrails[1].policy: required for guardrail kind 'llm_judge'" in error(
        doc, "hidden-mailbox"
    )
    doc = document("hidden-mailbox")
    hard_guardrail(doc, "input-sanitizer")["policy"] = "prompts/input_judge.md"
    assert "guardrails[0].policy: not used by guardrail kind 'sanitizer'" in error(
        doc, "hidden-mailbox"
    )


def test_duplicate_guardrail_ids():
    doc = document("hidden-mailbox")
    hard_guardrail(doc, "output-sanitizer")["id"] = "input-sanitizer"
    assert "guardrails[2].id: duplicate guardrail id 'input-sanitizer'" in error(
        doc, "hidden-mailbox"
    )


def test_unknown_injection_tool():
    doc = document("email-review")
    doc["attack_surface"]["injection"]["tool"] = "get_mails"
    message = error(doc, "email-review")
    assert message.startswith("levels/email-review/level.yaml: [easy] ")
    assert "attack_surface.injection.tool: unknown tool 'get_mails'" in message


def test_injection_tool_must_carry_content():
    doc = document("email-review")
    doc["attack_surface"]["injection"]["tool"] = "send_email"
    assert "injection.tool: tool 'send_email' cannot carry injected content" in error(
        doc, "email-review"
    )
    # An inbox with an injection slot needs get_emails as injection point.
    doc = document("email-review")
    doc["attack_surface"] = document("hidden-mailbox")["attack_surface"]
    assert error(doc, "email-review") == (
        "levels/email-review/data/inbox.yaml: an email is marked 'injection: true' but the injection point "
        "is not get_emails"
    )


def test_multi_turn_attack_needs_a_multi_turn_surface():
    doc = document("email-review")
    doc["red_team"]["attacks"].append({"type": "linear_jailbreaking", "attempts": 2})
    assert (
        "red_team.attacks[2].type: 'linear_jailbreaking' is a multi-turn attack type but the attack surface "
        "is single-turn"
    ) in error(doc, "email-review")


def test_unknown_kinds_name_the_vocabulary():
    doc = document("hidden-mailbox")
    doc["red_team"]["attacks"][0]["type"] = "jailbreak"
    assert (
        "red_team.attacks[0].type: unknown attack type 'jailbreak' (known: prompt_injection"
        in error(doc, "hidden-mailbox")
    )
    doc = document("hidden-mailbox")
    doc["agent"]["tools"][0]["kind"] = "drive"
    assert "agent.tools[0].kind: unknown tool kind 'drive'" in error(
        doc, "hidden-mailbox"
    )
    doc = document("hidden-mailbox")
    doc["success"]["kind"] = "secret_in_reply"
    assert "success.kind: unknown success check 'secret_in_reply'" in error(
        doc, "hidden-mailbox"
    )


def test_success_check_needs_its_inputs():
    doc = document("offer-review")
    doc["success"] = {"kind": "secret_in_output"}
    assert "success.kind: 'secret_in_output' needs a secret" in error(
        doc, "offer-review"
    )
    doc = document("hidden-mailbox")
    doc["success"] = {"kind": "secret_in_sent_email"}
    assert "success.kind: 'secret_in_sent_email' needs a send_email tool" in error(
        doc, "hidden-mailbox"
    )


def test_schema_errors_name_the_field():
    doc = document("hidden-mailbox")
    doc["agent"]["modell"] = doc["agent"].pop("model")
    message = error(doc, "hidden-mailbox")
    assert "[easy] agent.model: Field required" in message
    assert "[easy] agent.modell: Extra inputs are not permitted" in message
    doc = document("hidden-mailbox")
    doc["difficulties"]["hard"]["guardrails"][1]["model"]["params"]["temperature"] = (
        "cold"
    )
    assert "[hard] guardrails[1].model.params.temperature:" in error(
        doc, "hidden-mailbox"
    )


def test_surface_rules():
    doc = document("email-review")
    doc["attack_surface"]["turns"] = "multi"
    doc["attack_surface"]["max_turns"] = 5
    assert "attack_surface: an indirect surface is single-turn" in error(
        doc, "email-review"
    )
    doc = document("email-review")
    del doc["attack_surface"]["user_task"]
    assert "an indirect surface needs a fixed user_task" in error(doc, "email-review")


def test_level_id_and_difficulties():
    doc = document("hidden-mailbox")
    doc["id"] = "level-1"
    assert error(doc, "hidden-mailbox") == (
        "levels/hidden-mailbox/level.yaml: id: must equal the directory name 'hidden-mailbox'"
    )
    doc = document("hidden-mailbox")
    doc["difficulties"] = {}
    assert "difficulties: at least one difficulty is required" in error(
        doc, "hidden-mailbox"
    )


def test_missing_file_reference():
    doc = document("hidden-mailbox")
    doc["difficulties"]["medium"]["agent"]["security_prompt"] = (
        "prompts/security_medim.md"
    )
    assert error(doc, "hidden-mailbox") == (
        "levels/hidden-mailbox/level.yaml: [medium] agent.security_prompt: file not found: "
        "prompts/security_medim.md"
    )
    doc = document("hidden-mailbox")
    doc["refusal"] = "../../hackathon/config.py"
    assert "refusal: file is outside the levels directory" in error(
        doc, "hidden-mailbox"
    )


def test_unknown_placeholder_names_the_prompt_file(levels_copy):
    prompt = levels_copy / "hidden-mailbox" / "prompts" / "security_easy.md"
    prompt.write_text(prompt.read_text() + "Today is {{weekday}}.\n")
    with pytest.raises(LevelConfigError) as exc:
        load_levels(levels_copy)
    assert exc.value.file == "levels/hidden-mailbox/prompts/security_easy.md"
    assert "unknown placeholder {{weekday}}" in exc.value.problem
    assert (
        "levels/hidden-mailbox/level.yaml [easy] agent.security_prompt"
        in exc.value.problem
    )


def test_runtime_placeholder_outside_its_context(levels_copy):
    # {{content}} is a judge-policy placeholder; the agent's system prompt cannot use it.
    prompt = levels_copy / "email-review" / "prompts" / "system.md"
    prompt.write_text(prompt.read_text().replace("{{user_name}}", "{{content}}"))
    with pytest.raises(LevelConfigError) as exc:
        load_levels(levels_copy)
    assert exc.value.file == "levels/email-review/prompts/system.md"
    # The send-email policy may only use the parameters of the wrapped tool.
    shutil.copy(LEVELS_ROOT / "email-review/prompts/system.md", prompt)
    policy = levels_copy / "email-review" / "prompts" / "send_email_judge.md"
    policy.write_text(policy.read_text().replace("{{args.to}}", "{{args.recipient}}"))
    with pytest.raises(LevelConfigError) as exc:
        load_levels(levels_copy)
    assert exc.value.file == "levels/email-review/prompts/send_email_judge.md"
    assert "unknown placeholder {{args.recipient}}" in exc.value.problem


def test_invalid_yaml_and_invalid_data_file(levels_copy):
    data = levels_copy / "hidden-mailbox" / "data" / "drive.yaml"
    data.write_text("folders:\n- folder_name: /x/\n")
    with pytest.raises(LevelConfigError) as exc:
        load_levels(levels_copy)
    assert (
        str(exc.value)
        == "levels/hidden-mailbox/data/drive.yaml: folders[0].files: Field required"
    )
    shutil.copy(LEVELS_ROOT / "hidden-mailbox/data/drive.yaml", data)
    (levels_copy / "offer-review" / "level.yaml").write_text(
        "id: offer-review\n  title: ["
    )
    with pytest.raises(LevelConfigError) as exc:
        load_levels(levels_copy)
    assert str(exc.value).startswith("levels/offer-review/level.yaml: invalid YAML")


def test_security_prompt_needs_a_placeholder(levels_copy):
    prompt = levels_copy / "offer-review" / "prompts" / "system.md"
    prompt.write_text(prompt.read_text().replace("{{security_prompt}}", ""))
    with pytest.raises(LevelConfigError) as exc:
        load_levels(levels_copy)
    assert exc.value.file == "levels/offer-review/prompts/system.md"
    assert "has no {{security_prompt}} placeholder" in exc.value.problem


def test_copied_levels_load_like_the_originals(levels_copy):
    copied, original = load_levels(levels_copy), load_levels()
    assert (
        list(copied)
        == list(original)
        == [
            "hidden-mailbox",
            "email-review",
            "offer-review",
            "hidden-mailbox-output-judge",
        ]
    )
    for level_id, level in original.items():
        for difficulty, variant in level.variants.items():
            assert copied[level_id].variants[difficulty] == variant


def test_variant_merge_replaces_lists_and_merges_dicts():
    base = {
        "agent": {"name": "a", "model": {"model": "m", "params": {"temperature": 0}}},
        "guardrails": [1, 2],
    }
    variant = {"agent": {"model": {"params": {"seed": 1}}}, "guardrails": [3]}
    assert deep_merge(base, variant) == {
        "agent": {
            "name": "a",
            "model": {"model": "m", "params": {"temperature": 0, "seed": 1}},
        },
        "guardrails": [3],
    }
    assert base["guardrails"] == [1, 2]


def test_variant_can_override_a_model_parameter_of_one_role():
    doc = document("hidden-mailbox")
    doc["difficulties"]["hard"]["guardrails"][1]["model"] = {
        "model": "gemini-3.8-flash",
        "params": {"temperature": 0.4, "frequency_penalty": 0.5, "seed": 7},
    }
    doc["difficulties"]["easy"]["agent"]["model"] = {"params": {"top_p": 0.9}}
    level = load_level_document(doc, LEVELS_ROOT / "hidden-mailbox")
    hard = level.variants["hard"]
    assert hard.guardrails[1].model.params.compact() == {
        "temperature": 0.4,
        "frequency_penalty": 0.5,
        "seed": 7,
    }
    assert hard.agent.model.params.compact() == {
        "max_tokens": 512,
        "extra": {"chat_template_kwargs": {"enable_thinking": False}},
    }
    assert level.variants["easy"].agent.model.params.compact() == {
        "max_tokens": 512,
        "extra": {"chat_template_kwargs": {"enable_thinking": False}},
        "top_p": 0.9,
    }
    assert hard.red_team.attacker.params.compact() == {
        "temperature": 0.7,
        "max_tokens": 512,
        "extra": {"chat_template_kwargs": {"enable_thinking": False}},
    }


def test_injection_content_is_validated():
    level = load_levels()["email-review"].variants["easy"]
    assert list(level.injection_content({"body": "<attack 1>", "subject": "<s>"})) == [
        "subject",
        "body",
    ]
    with pytest.raises(InjectionError, match="exceeds 300 characters"):
        level.injection_content({"subject": "x" * 301, "body": ""})
    with pytest.raises(InjectionError, match="unknown injection field"):
        level.injection_content({"subject": "", "body": "", "cc": ""})
    with pytest.raises(InjectionError, match="must be a string"):
        level.injection_content({"subject": ""})


def test_templates():
    assert placeholders("a {{ x }} b {{args.to}} {x} {") == ["x", "args.to"]
    with pytest.raises(TemplateError, match="malformed placeholder"):
        placeholders("a {{x y}}")
    assert render_partial("{{a}} {{b}}", {"a": "{{b}}"}, {"b"}) == "{{b}} {{b}}"
    with pytest.raises(TemplateError, match=r"unknown placeholder \{\{c\}\}"):
        render_partial("{{c}}", {"a": "1"}, {"b"})
    assert render("{{a}}-{{args.to}}", {"a": 1, "args.to": "x"}) == "1-x"
    with pytest.raises(TemplateError, match="missing value"):
        render("{{a}}", {})


def test_every_llm_role_defaults_to_qwen_on_leonardo():
    roles = [
        role
        for level_set in load_levels().values()
        for resolved in level_set.variants.values()
        for role in resolved.model_roles()
    ]
    assert {r["role"] for r in roles} >= {
        "agent",
        "red_team.attacker",
        "red_team.simulator",
        "red_team.evaluator",
        "green_team.generator",
    }
    assert any(r["role"].startswith("guardrail:") for r in roles)
    for role in roles:
        assert role["model"] == "qwen3-235b-a22b-fp8-leonardo", role["role"]
        assert role["endpoint"] == "agentgateway", role["role"]
        # vLLM serves this model with a 2,048-token context and rejects Gemini-only settings.
        assert role["params"]["max_tokens"] <= 1024, role["role"]
        assert "reasoning_effort" not in role["params"], role["role"]
        assert role["params"]["extra"] == {
            "chat_template_kwargs": {"enable_thinking": False}
        }, role["role"]
