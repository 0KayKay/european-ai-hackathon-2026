from types import SimpleNamespace

import pytest
from ag_ui.core import TextMessageStartEvent, TextMessageContentEvent, TextMessageEndEvent
from pydantic_ai import Agent

from level_builder import LevelConfig, get_level, list_levels
from level_builder.config import CONFIG_DIR, _read
from hackathon.agent.level_runtime import register_tools, run_guardrails


def test_level_files_define_the_existing_variants() -> None:
    levels = {level.id: level for level in list_levels()}
    assert set(levels) == {"EASY", "MEDIUM", "HARD"}
    assert not levels["EASY"].guardrails
    assert not levels["MEDIUM"].guardrails
    assert [(guard.id, guard.type, guard.hook) for guard in levels["HARD"].guardrails] == [
        ("input_scope", "llm_judge", "input")
    ]
    assert "Only list and read files" in get_level("MEDIUM").system_prompt


@pytest.mark.asyncio
async def test_custom_hooks_normalize_input_and_block_selected_tool() -> None:
    level = LevelConfig.model_validate(_read(CONFIG_DIR / "_CUSTOM_EXAMPLE.toml"))
    deps = {"user_id": "user", "mailbox": "secret@example.com", "guardrail_events": []}
    normalized = await run_guardrails(level, "input", "Ｆi\u200bles", deps)
    assert normalized.value == "Files"
    assert deps["guardrail_events"][0]["stage"] == "normalize_input"

    agent = Agent("test")
    register_tools(agent, level)
    tool = agent._function_toolset.tools["get_file_content"].function
    result = await tool(
        SimpleNamespace(deps=deps),
        "/Finanzen/Legacy/internal_only/",
        "prozess_notizen_legacy.txt",
    )
    assert result == "This tool cannot read internal-only files."
    assert deps["guardrail_events"][-1]["stage"] == "block_legacy_tool"
    assert deps["guardrail_events"][-1]["status"] == "error"


@pytest.mark.asyncio
async def test_output_guard_transforms_text_before_it_is_streamed(monkeypatch) -> None:
    for key, value in {
        "AGENTGATEWAY_URL": "http://gateway/v1",
        "AGENTGATEWAY_API_KEY": "test-key",
        "GOOGLE_STORAGE_BUCKET": "",
        "PRETTY_DOMAIN": "localhost",
        "DB_USER": "app",
        "DB_PASSWORD": "test",
        "DB_NAME": "app",
    }.items():
        monkeypatch.setenv(key, value)
    from hackathon.config import get_settings
    get_settings.cache_clear()
    from hackathon.api.router import apply_output_guards
    level = LevelConfig.model_validate({**get_level("EASY").model_dump(),
        "guardrails": [
            {
                "id": "normalize_output", "name": "Normalize output", "type": "custom",
                "hook": "output", "entrypoint": "hackathon.level_plugins.normalization:normalize_text",
            }
        ]
    })
    deps = {"user_id": "user", "guardrail_events": []}

    async def source():
        yield TextMessageStartEvent(message_id="answer", role="assistant")
        yield TextMessageContentEvent(message_id="answer", delta="Ｆi")
        yield TextMessageContentEvent(message_id="answer", delta="\u200bles")
        yield TextMessageEndEvent(message_id="answer")

    output = [event async for event in apply_output_guards(source(), level, deps)]
    assert [event.delta for event in output if isinstance(event, TextMessageContentEvent)] == ["Files"]
    assert deps["guardrail_events"][0]["stage"] == "normalize_output"

    async def interrupted_source():
        from ag_ui.core import RunFinishedEvent

        yield TextMessageContentEvent(message_id="answer", delta="Ｆiles")
        yield RunFinishedEvent(thread_id="thread", run_id="run")

    output = [event async for event in apply_output_guards(interrupted_source(), level, deps)]
    assert [event.delta for event in output if isinstance(event, TextMessageContentEvent)] == ["Files"]
    assert isinstance(output[0], TextMessageStartEvent)
    assert isinstance(output[2], TextMessageEndEvent)
