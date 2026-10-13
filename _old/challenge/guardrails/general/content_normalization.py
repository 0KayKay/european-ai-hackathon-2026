import structlog
from ag_ui.core import ToolMessage
from langchain.agents.middleware import AgentMiddleware, AgentState
from langgraph.runtime import Runtime
from langchain_core.messages import AIMessage, AIMessageChunk
from structlog import BoundLogger

from typing import Any

logger: BoundLogger = structlog.get_logger("content_normalization_middleware")


class ContentNormalizationMiddleware(AgentMiddleware):
    """
    Normalizes AIMessage content from list format to string format for backward compatibility.

    Attributes:
        include_thinking_content_in_message (str): Whether to include the thinking content in the message content.

    Background:
    -----------
    When reasoning/thinking capabilities are enabled in LLM models (e.g., Gemini with reasoning,
    Claude with extended thinking), the AIMessage.content field changes from a simple string
    to a list of content blocks with different types:

    Before (string format):
        AIMessage(content="Hello! I can help you with that.")

    After (list format with reasoning):
        AIMessage(content=[
            {"type": "thinking", "thinking": "The user wants help. I should..."},
            {"type": "text", "text": "Hello! I can help you with that."}
        ])

    Problem:
    --------
    This structural change is a breaking change that affects all downstream code expecting
    string content, including:
    - Everything that processes LangGraph output messages, especially existing LangGraph middlewares that assume
      content is a string

    Solution:
    ---------
    This middleware intercepts all AIMessages after model generation and normalizes the
    content back to string format by:
    1. Extracting only "text" type blocks (the actual response to the user)
    2. Discarding "thinking" type blocks (internal reasoning process)
    3. Preserving all other message attributes (metadata, tool calls, etc.)

    This ensures backward compatibility with existing middlewares while allowing reasoning-
    enabled models to work seamlessly. Thinking/reasoning content can be handled separately
    by specialized handlers (e.g., UI components that display reasoning to users).

    Usage:
    ------
    Add this middleware FIRST in your middleware chain to ensure all downstream middlewares
    receive normalized string content:

        agent = create_react_agent(
            model=your_model,
            tools=your_tools,
            middlewares=[
                ContentNormalizationMiddleware(),  # First!
                YourOtherMiddleware1(),
                YourOtherMiddleware2(),
            ]
        )

    Note:
    -----
    - This middleware only affects AIMessages with list-format content
    - Messages with string content pass through unchanged
    - All other message types (HumanMessage, SystemMessage, ToolMessage) are preserved as-is
    - If you need access to thinking content, it can be accessed via response_metadata["thinking"] of the AIMessage
      if any

    Model Support:
    --------------
    - Google Gemini: Uses reasoning internally, may or may not expose thinking blocks [tested]
    - Anthropic Claude: Exposes thinking blocks when extended thinking is enabled [says AI]
    - OpenAI o1/o3: Uses reasoning internally, typically not exposed in API [says AI]
    - Other providers: May have varying implementations of reasoning content [says AI]
    """
    def __init__(self):
        super().__init__()
        self.include_thinking_content_in_message = False

    async def aafter_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        logger.debug("Run after_model.")
        try:
            if len(state["messages"]) == 0:
                return None
            for message in state["messages"]:
                if isinstance(message, AIMessage) and message.content and isinstance(message.content, list):
                    # record what was thinking in metadata
                    new_content = ""
                    message.response_metadata["_thinking"] = ""
                    for content_block in message.content:
                        if isinstance(content_block, dict):
                            block_type = content_block.get("type")
                            if block_type == "thinking":
                                thinking = content_block.get("thinking", "")
                                if self.include_thinking_content_in_message:
                                    new_content += thinking
                                message.response_metadata["_thinking"] += thinking
                            elif block_type == "text":
                                new_content += content_block.get("text", "")
                        elif isinstance(content_block, str):
                            new_content += content_block
                    message.content = new_content
                    logger.debug(f"Normalized message: {message}")
            return {
                "messages": state["messages"]
            }
        except Exception as e:
            logger.error(f"Error in abefore_model guardrail: {e}", exc_info=True)
            return None
