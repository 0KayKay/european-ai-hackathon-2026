from typing import Callable, override

import structlog
from structlog import BoundLogger
from langchain.agents.middleware import AgentState
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.runtime import Runtime
from langchain_core.messages import AIMessage, ToolMessage, RemoveMessage, HumanMessage
from langgraph.types import Command

from _old.challenge.guardrails.tracked_agent_guardrail import TrackedAgentMiddleware

logger: BoundLogger = structlog.get_logger("deny_guardrail")


class TrackedDenyGuardrailMiddleware(TrackedAgentMiddleware):
    """
    Dummy Guardrail Middleware.

    Args:
        after_model: Whether to apply the guardrail on the model output.
        wrap_tool_call: Whether to apply the guardrail when wrapping tool calls.
        before_model: Whether to apply the guardrail before the model.
        allow_error: Whether to allow the guardrail to fail.
        continue_graph_execution: Whether to continue the agent execution if the guardrail fails. If False, jumps to end.
    """
    guardrail_name = "TrackedDenyGuardrail"

    before_model_can_jump_to = ["end"]
    after_model_can_jump_to = ["end"]

    def __init__(self, after_model: bool = False, wrap_tool_call: bool = False, before_model: bool = False,
                 allow_error: bool = False, continue_graph_execution: bool = False):
        super().__init__()
        if not after_model and not wrap_tool_call and not before_model:
            raise ValueError(
                "One of input parameters after_model, wrap_tool_call, before_model must be True. "
                "Otherwise, middleware has no effect"
            )

        self.before_model = before_model
        self.after_model = after_model
        self.wrap_tool_call = wrap_tool_call

        self.allow_error = allow_error
        self.continue_graph_execution = continue_graph_execution

    @override
    async def _abefore_model(self, state: AgentState, runtime: Runtime):
        try:
            if not self.before_model:
                return None
            if len(state["messages"]) == 0:
                return None

            # Check user input (last human message)
            last_message = state["messages"][-1]
            if not isinstance(last_message, HumanMessage) or not last_message.content:
                return None

            if await self._output_is_unsafe(last_message.content):
                logger.warning("Unsafe content detected in user input, blocking request")

                safe_message = AIMessage(
                    name="assistant",
                    content="I cannot process that request because it contains unsafe content. Please rephrase your request.")

                new_state = {
                    "messages": [safe_message]
                }
                if not self.continue_graph_execution:
                    new_state["jump_to"] = "end"
                return new_state, "blocked"
            return None, "passed"
        except Exception as e:
            logger.error(f"Error in abefore_model guardrail: {e}", exc_info=True)
            if self.allow_error:
                return None
            safe_message = AIMessage(
                name="assistant",
                content="I cannot process that request because there was an error. Please retry later.")
            new_state = {
                "messages":
                    [safe_message]
            }
            return new_state, "blocked", "Blocked due to error in abefore_model guardrail: {e}"

    @override
    async def _awrap_tool_call(self,
                               request: ToolCallRequest,
                               handler: Callable[[ToolCallRequest], ToolMessage | Command]):
        """
        Check if the tool call output is unsafe and block it if it is by replacing it with a new
        tool message with a blocked content.

        NOTE: Assumes that our tools always return ToolMessage and no Command.
        """
        logger.debug("Run wrap_tool_call guardrail.")
        try:
            # Always call the handler first to get the result
            result = await handler(request)

            if not self.wrap_tool_call:
                return result, None

            # Check if result is a ToolMessage with content to evaluate
            if isinstance(result, ToolMessage) and result.content:
                if await self._output_is_unsafe(result.content):
                    logger.warning(f"Unsafe content detected in tool output: {request.tool.name}")
                    new_tool_message = ToolMessage(
                        tool_call_id=request.tool_call["id"],
                        content="Tool result blocked by safety guardrail."
                    )
                    return new_tool_message, "blocked"
            return result, "passed"
        except Exception as e:
            logger.error(f"Error in awrap_tool_call guardrail: {e}", exc_info=True)
            new_tool_message = ToolMessage(
                content="There was an error evaluating the tool call.",
                tool_call_id=request.tool_call["id"]
            )
            return new_tool_message, "blocked", f"Blocked due to error in awrap_tool_call guardrail: {e}"

    @override
    async def _aafter_model(self, state: AgentState, runtime: Runtime):
        logger.debug("Checking after model")
        try:
            if not self.after_model:
                return None
            if len(state["messages"]) == 0:
                return None

            # Check model output (last human message)
            last_message = state["messages"][-1]
            if not isinstance(last_message, AIMessage) or not last_message.content:
                return None

            if await self._output_is_unsafe(last_message.content):
                logger.warning("Unsafe content detected in model output, blocking request")
                safe_message = AIMessage(
                    content="I cannot process that request because it contains unsafe content. Please rephrase your request.",
                    name=last_message.name)

                new_state = {
                    "messages": [RemoveMessage(id=last_message.id), safe_message],
                }
                if not self.continue_graph_execution:
                    new_state["jump_to"] = "end"
                return new_state, "blocked"
            return None, "passed"
        except Exception as e:
            logger.error(f"Error in aafter_model guardrail: {e}", exc_info=True)
            if self.allow_error:
                return None
            last_message = state["messages"][-1]
            safe_message = AIMessage(
                name=last_message.name,
                content="I cannot process that request because there was an error. Please retry later.")
            new_state = {
                "messages":
                    [RemoveMessage(id=last_message.id), safe_message]
            }
            return new_state, "blocked", "Blocked due to error in abefore_model guardrail: {e}"

    async def _output_is_unsafe(self, content: str) -> bool:
        return True
