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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "updates",
    [
        [
            update(
                contents=[
                    content(
                        "error",
                        message="Content filter triggered. Finish reason: 'content_filter'",
                    )
                ]
            )
        ],
        [update(finish_reason="content_filter")],
    ],
)
async def test_content_filter_is_a_blocked_answer(monkeypatch, updates) -> None:
    monkeypatch.setattr(agui_target, "AGUIChatClient", FakeAGUIChatClient)
    FakeAGUIChatClient.updates = updates

    result = await agui_target.call_agui(
        "http://backend:8000/api/v1/ag-ui", {}, {}, "attack", thread_id="thread-1"
    )

    assert result.filtered is True
    assert result.text == agui_target.CONTENT_FILTER_RESPONSE
    assert result.tool_names == []


@pytest.mark.asyncio
async def test_other_target_errors_still_fail(monkeypatch) -> None:
    monkeypatch.setattr(agui_target, "AGUIChatClient", FakeAGUIChatClient)
    FakeAGUIChatClient.updates = [
        update(contents=[content("error", message="Database connection failed")])
    ]

    with pytest.raises(RuntimeError, match="Database connection failed"):
        await agui_target.call_agui(
            "http://backend:8000/api/v1/ag-ui", {}, {}, "attack", thread_id="thread-1"
        )


@pytest.mark.asyncio
async def test_observes_raw_agui_events_without_changing_answer(monkeypatch) -> None:
    class EventClient(FakeAGUIChatClient):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)

            async def post_run(*args, **kwargs):
                yield {
                    "type": "CUSTOM",
                    "name": "challenge.progress",
                    "value": {"stage": "guardrail", "status": "success"},
                }

            self._http_service = SimpleNamespace(post_run=post_run)

        async def get_response(self, messages, *, stream, options):
            async for _ in self._http_service.post_run():
                pass
            yield update(contents=[content("text", text="safe")])
            yield update(finish_reason="stop")

    monkeypatch.setattr(agui_target, "AGUIChatClient", EventClient)
    observed = []
    result = await agui_target.call_agui(
        "http://backend:8000/api/v1/ag-ui",
        {},
        {},
        "attack",
        thread_id="thread-1",
        on_event=observed.append,
    )
    assert result.text == "safe"
    assert observed[0]["value"] == {"stage": "guardrail", "status": "success"}
