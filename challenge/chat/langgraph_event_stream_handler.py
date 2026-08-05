from typing import Any

from chainlit import Message, Step
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
import structlog
from structlog.stdlib import BoundLogger

logger: BoundLogger = structlog.get_logger(__name__)


class LangGraphStreamHandler:
    """Handles streaming events from LangGraph agents and updates Chainlit UI."""

    def __init__(self):
        self.response_message = Message(content="")
        self.current_content = ""
        self.tool_calls_made = {}  # Map tool_call_id to tool call details
        self.thinking_step = None

    def _handle_tool_calls(self, message_chunk: AIMessageChunk | AIMessage):
        logger.debug(f"Tool calls detected: {message_chunk.tool_calls}")
        for tool_call in message_chunk.tool_calls:
            tool_call_id = tool_call.get("id")
            if tool_call_id and tool_call_id not in self.tool_calls_made:
                # Store the tool call details for later matching
                self.tool_calls_made[tool_call_id] = {
                    "name": tool_call.get("name", "unknown"),
                    "args": tool_call.get("args", {}),
                }
                logger.debug(
                    f"Tool call detected: {tool_call.get('name', 'unknown')} with args: {tool_call.get('args', {})}")

    async def _handle_tool_message(self, message_chunk: ToolMessage, metadata):
        # make sure thinking step is closed before handling tool message, otherwise tool message
        # of thinking step will be nested in thinking
        await self._close_thinking_step()
        tool_call_id = message_chunk.tool_call_id if hasattr(message_chunk, "tool_call_id") else None

        # Try to find the matching tool call
        if tool_call_id and tool_call_id in self.tool_calls_made:
            tool_info = self.tool_calls_made[tool_call_id]
            tool_name = tool_info["name"]
            tool_args = tool_info["args"]
        else:
            # Fallback to metadata if we can't find the tool call
            tool_name = metadata.get("langgraph_node", "unknown_tool")
            tool_args = {}

        await self._stream_tool_message(message_chunk, tool_name, tool_args)
        logger.debug(f"Tool executed: {tool_name}")

    async def _stream_ai_message_chunk(self, message_chunk: AIMessageChunk):
        self.current_content += message_chunk.content
        await self.response_message.stream_token(message_chunk.content)

    async def _stream_ai_message(self, message_chunk: AIMessage):
        await self._finalize_chainlit_message()
        # self.current_content += message_chunk.content
        self.response_message = Message(content=message_chunk.content)
        await self.response_message.send()
        self.current_content += message_chunk.content

    async def _stream_tool_message(self, message_chunk: ToolMessage, tool_name: str, tool_args: dict):
        # finish current message so that tool call can be streamed in between AI messages
        await self._finalize_chainlit_message()
        # init new chainlit response message for next message
        self.response_message = Message(content="")

        ## define parameters before async with block to avoid race conditions ##
        step = Step(name=f"Tool: {tool_name}", type="tool")
        # Show the actual tool call arguments as input
        step.input = str(tool_args) if tool_args else "{}"
        # Show the tool's output
        step.output = str(message_chunk.content)

        async with step:
            await step.send()

    async def _close_thinking_step(self):
        if self.thinking_step:
            await self.thinking_step.send()
            await self.thinking_step.__aexit__(None, None, None)
            self.thinking_step = None

    async def _stream_thinking_chunk(self, l_message_chunk: AIMessageChunk):
        """
        Handles thinking and exits thinking content properly as soon as thinking is done.
        """
        if not self.thinking_step:
            self.thinking_step = Step(name="Thinking", type="tool")
            self.thinking_step.output = ""
            await self.thinking_step.__aenter__()
        for message_data in l_message_chunk.content:
            if message_data.get("type") == "thinking":
                self.thinking_step.output += message_data.get("thinking")
                await self.thinking_step.update()
            else:
                await self._close_thinking_step()
                if message_data.get("type") == "text":
                    new_message = AIMessageChunk(content=message_data.get("text"),
                                                tool_calls=message_data.get("tool_calls", []))

                    await self._stream_ai_message_chunk(new_message)
                    if hasattr(new_message, "tool_calls") and new_message.tool_calls:
                        self._handle_tool_calls(new_message)


    async def handle_stream_event(self, event: dict[str, Any]) -> None:
        """Process a single streaming event from LangGraph.

        Args:
            event: Event dict from agent.astream() with stream_mode="messages"
        """
        # event is a tuple of (message_chunk, metadata)
        message_chunk, metadata = event

        # ignore safety check messages
        if "guardrail" in metadata.get("tags", {}):
            return

        # Handle AI message chunks (streaming tokens)
        if isinstance(message_chunk, AIMessageChunk):
            logger.debug(f"AIMessageChunk: {message_chunk.content}")
            # Track tool calls if present
            if hasattr(message_chunk, "tool_calls") and message_chunk.tool_calls:
                self._handle_tool_calls(message_chunk)
            if message_chunk.content and isinstance(message_chunk.content, str):
                await self._close_thinking_step()
                await self._stream_ai_message_chunk(message_chunk)
            elif message_chunk.content and isinstance(message_chunk.content, list):
                await self._stream_thinking_chunk(message_chunk)
        # Handle Middleware outputs
        elif isinstance(message_chunk, AIMessage):
            logger.debug(f"AIMessage: {message_chunk.content}")
            # Track tool calls if present
            if hasattr(message_chunk, "tool_calls") and message_chunk.tool_calls:
                self._handle_tool_calls(message_chunk)
            if message_chunk.content and isinstance(message_chunk.content, str):
                await self._stream_ai_message(message_chunk)
        # Handle tool messages (tool execution results)
        elif isinstance(message_chunk, ToolMessage):
            await self._handle_tool_message(message_chunk, metadata)


    async def _finalize_chainlit_message(self):
        if self.response_message.content:
            await self.response_message.send()
            self.current_content = ""
        else:
            await self.response_message.remove()

    async def finalize(self) -> str:
        """Finalize the message and return the complete content.

        Returns:
            The complete message content
        """
        if self.current_content:
            self.response_message.content = self.current_content
            await self.response_message.send()
        else:
            # If no content was streamed, remove the empty message
            await self.response_message.remove()

        return self.current_content

    async def handle_error(self, error: Exception) -> None:
        """Handle errors during streaming.

        Args:
            error: The exception that occurred
        """
        logger.error(f"Error during streaming: {error}")
        error_msg = f"An error occurred: {str(error)}"

        if not self.current_content:
            # If we haven't sent anything yet, send error message
            self.response_message.content = error_msg
            await self.response_message.send()
        else:
            # If we've already started streaming, append error
            await self.response_message.stream_token(f"\n\n{error_msg}")
            await self.response_message.send()


async def create_stream_handler() -> LangGraphStreamHandler:
    """Create a new stream handler for LangGraph events.

    Returns:
        A configured LangGraphStreamHandler instance
    """
    return LangGraphStreamHandler()
