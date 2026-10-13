from types import SimpleNamespace

import pytest

from hackathon_deepteam import agui_target


class FakeAGUIChatClient:
    updates = []
    messages = []
    options = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def get_response(self, messages, *, stream, options):
        self.messages = messages
        self.__class__.options = options
        for update in self.updates:
            yield update


def update(role="assistant", contents=(), finish_reason=None):
    return SimpleNamespace(
        role=role, contents=list(contents), finish_reason=finish_reason
    )


def content(type, *, text=None, name=None, message=None):
    return SimpleNamespace(type=type, text=text, name=name, message=message)


@pytest.mark.asyncio
async def test_collects_answer_and_tools(monkeypatch) -> None:
    monkeypatch.setattr(agui_target, "AGUIChatClient", FakeAGUIChatClient)
    FakeAGUIChatClient.updates = [
        update(contents=[content("text", text="Final ")]),
        update(contents=[content("function_call", name="search")]),
        update(contents=[content("text", text="answer")]),
        update(finish_reason="stop"),
    ]
    result = await agui_target.call_agui(
        "http://backend:8000/api/v1/ag-ui",
        {"Cookie": "session=test"},
        {"settings": {"modelId": "test"}},
        "attack",
        thread_id="thread-1",
    )
    assert result == agui_target.AGUIResult("Final answer", ["search"])
    assert FakeAGUIChatClient.options == {"metadata": {"thread_id": "thread-1"}}


@pytest.mark.asyncio
async def test_rejects_incomplete_stream(monkeypatch) -> None:
    monkeypatch.setattr(agui_target, "AGUIChatClient", FakeAGUIChatClient)
    FakeAGUIChatClient.updates = [update(contents=[content("text", text="partial")])]
    with pytest.raises(RuntimeError, match="without RUN_FINISHED"):
        await agui_target.call_agui(
            "http://backend:8000/api/v1/ag-ui", {}, {}, "attack", thread_id="thread-1"
        )
