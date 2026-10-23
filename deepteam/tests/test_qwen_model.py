import asyncio
from threading import Event

import pytest

from hackathon_deepteam.qwen_model import LocalQwenModel
from hackathon_deepteam.scan_config import AttackerConfig


def test_qwen_error_cancels_other_requests_and_blocks_new_ones(monkeypatch):
    calls = []

    async def scenario():
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def fake_acompletion(**request):
            prompt = request["messages"][0]["content"]
            calls.append(prompt)
            if prompt == "boom":
                raise RuntimeError("Ollama failed")
            started.set()
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        monkeypatch.setattr("hackathon_deepteam.qwen_model.acompletion", fake_acompletion)
        model = LocalQwenModel(AttackerConfig(), "http://localhost:11434")
        slow = asyncio.create_task(model.a_generate("slow"))
        await started.wait()

        with pytest.raises(RuntimeError, match="Ollama failed"):
            await model.a_generate("boom")
        with pytest.raises(asyncio.CancelledError):
            await slow
        with pytest.raises(asyncio.CancelledError):
            await model.a_generate("later")
        assert cancelled.is_set()

    asyncio.run(scenario())
    assert calls == ["slow", "boom"]


def test_external_stop_cancels_inflight_qwen_request(monkeypatch):
    stop_event = Event()

    async def scenario():
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def fake_acompletion(**request):
            started.set()
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        monkeypatch.setattr("hackathon_deepteam.qwen_model.acompletion", fake_acompletion)
        model = LocalQwenModel(
            AttackerConfig(), "http://localhost:11434", stop_event=stop_event
        )
        request = asyncio.create_task(model.a_generate("slow"))
        await started.wait()
        stop_event.set()
        with pytest.raises(asyncio.CancelledError):
            await request
        assert cancelled.is_set()

    asyncio.run(scenario())
