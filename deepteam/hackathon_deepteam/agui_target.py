"""Map DeepTeam turns to an AG-UI target and collect its answer."""

from dataclasses import dataclass
from typing import Any, Callable, Protocol
from uuid import NAMESPACE_URL, uuid5

import httpx
from agent_framework import Message
from agent_framework.ag_ui import AGUIChatClient, state_carrier


class TurnLike(Protocol):
    role: str
    content: str


@dataclass(frozen=True)
class AGUIResult:
    text: str
    tool_names: list[str]
    filtered: bool = False


CONTENT_FILTER_RESPONSE = "[Gemini blocked this response with its content filter; no answer was returned.]"


def is_content_filter_error(message: str) -> bool:
    normalized = message.casefold()
    return "content_filter" in normalized or "content filter triggered" in normalized


def thread_id_for(run_id: str, prompt: str, turns: list[TurnLike] | None) -> str:
    first_user = next(
        (turn.content for turn in (turns or []) if turn.role == "user"), prompt
    )
    return str(uuid5(NAMESPACE_URL, f"{run_id}:{first_user}"))


async def call_agui(
    url: str,
    headers: dict[str, str],
    state: dict[str, Any],
    prompt: str,
    *,
    thread_id: str,
    turns: list[TurnLike] | None = None,
    on_event: Callable[[dict[str, Any]], None] | None = None,
) -> AGUIResult:
    messages = [
        Message(role=turn.role, contents=[turn.content])
        for turn in (turns or [])
        if turn.role in ("user", "assistant")
    ]
    if not turns or turns[-1].role != "user" or turns[-1].content != prompt:
        messages.append(Message(role="user", contents=[prompt]))
    messages.append(Message(role="user", contents=[state_carrier(state)]))

    chunks: list[str] = []
    tool_names: dict[str, None] = {}
    finished = False
    async with httpx.AsyncClient(headers=headers, timeout=120, trust_env=False) as http:
        async with AGUIChatClient(endpoint=url, http_client=http, timeout=120) as agent:
            if on_event is not None:
                original_post_run = agent._http_service.post_run

                async def observed_post_run(*args, **kwargs):
                    async for event in original_post_run(*args, **kwargs):
                        on_event(event)
                        yield event

                agent._http_service.post_run = observed_post_run
            async for update in agent.get_response(
                messages,
                stream=True,
                options={"metadata": {"thread_id": thread_id}},
            ):
                for content in update.contents:
                    if content.type == "error":
                        message = str(getattr(content, "message", content))
                        if is_content_filter_error(message):
                            return AGUIResult(CONTENT_FILTER_RESPONSE, [], filtered=True)
                        raise RuntimeError(
                            f"AG-UI RUN_ERROR: {message}"
                        )
                    if (
                        content.type == "text"
                        and update.role == "assistant"
                        and content.text
                    ):
                        chunks.append(content.text)
                    elif content.type == "function_call" and content.name:
                        tool_names.setdefault(content.name, None)
                if update.finish_reason == "stop":
                    finished = True
                elif update.finish_reason == "content_filter":
                    return AGUIResult(CONTENT_FILTER_RESPONSE, [], filtered=True)
                elif update.finish_reason is not None:
                    raise RuntimeError(f"AG-UI run ended with {update.finish_reason}")

    if not finished:
        raise RuntimeError("AG-UI stream ended without RUN_FINISHED")
    answer = "".join(chunks)
    if not answer.strip():
        raise RuntimeError("AG-UI run finished without assistant text")
    return AGUIResult(text=answer, tool_names=list(tool_names))
