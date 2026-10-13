from collections.abc import AsyncIterable, Awaitable, Callable
from typing import Any

from chainlit import Message
from pydantic_ai import RunContext
from pydantic_ai.agent import EventStreamHandler
from pydantic_ai.messages import (
    AgentStreamEvent,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
)


def text_stream_handler(msg: Message) -> Callable[[AgentStreamEvent], Awaitable[None]]:
    """Handles the text part of the agent's response stream and updates the message."""

    async def handle(event: AgentStreamEvent) -> None:
        if (
            isinstance(event, PartStartEvent)
            and isinstance(event.part, TextPart)
            and event.part.content
        ):
            await msg.stream_token(event.part.content)
        elif (
            isinstance(event, PartDeltaEvent)
            and isinstance(event.delta, TextPartDelta)
            and event.delta.content_delta
        ):
            await msg.stream_token(event.delta.content_delta)

    return handle


async def handle_event_stream() -> tuple[EventStreamHandler, Any]:
    """Handles the response stream from the agent and sends messages to the user."""
    response_message = Message(content="", elements=[])
    handle_text_stream = text_stream_handler(response_message)

    async def handle_cleanup() -> None:
        if response_message.content:
            await response_message.send()
        else:
            await response_message.remove()

    async def handle_stream(
        ctx: RunContext, event_stream: AsyncIterable[AgentStreamEvent]
    ) -> None:
        try:
            async for event in event_stream:
                await handle_text_stream(event)
        finally:
            await handle_cleanup()

    return handle_stream, handle_cleanup
