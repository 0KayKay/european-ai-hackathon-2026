from typing import Any, Callable, List

import structlog
from structlog import BoundLogger
from langchain.agents.middleware import AgentMiddleware, AgentState, hook_config
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.runtime import Runtime
from langchain_core.messages import AIMessage, ToolMessage, RemoveMessage
from langgraph.types import Command

logger: BoundLogger = structlog.get_logger("deny_guardrail")


class DenyGuardrailMiddleware(AgentMiddleware):
    """
    Dummy Guardrail Middleware.

    Args:
        after_model: Whether to apply the guardrail on the model output.
        wrap_tool_call: Whether to apply the guardrail when wrapping tool calls.
        before_model: Whether to apply the guardrail before the model.
        allow_error: Whether to allow the guardrail to fail.
        continue_graph_execution: Whether to continue the agent execution if the guardrail fails. If False, jumps to end.
    """

    def __init__(self, after_model: bool = False, wrap_tool_call: bool = False, before_model: bool = False,
                 allow_error: bool = False, continue_graph_execution: bool = False, whitelisted_domains: List[str] = []):
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

        self.whitelisted_domains = whitelisted_domains

    @hook_config(can_jump_to=["end"])
    async def abefore_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        try:
            if not self.before_model:
                return None
            if len(state["messages"]) == 0:
                return None
            # allow internal users to still access app
            user = runtime.context.get("user", {})
            if user and any([user.identifier.endswith(f"@{domain}") for domain in self.whitelisted_domains]):
                return None

            # Check user input (last human message)
            last_message = state["messages"][-1]
            if last_message.type != "human" or not last_message.content:
                return None

            if await self._output_is_unsafe(last_message.content):
                logger.warning("Unsafe content detected in user input, blocking request")
                safe_message = AIMessage(
                    content="Die Challenge ist beendet. Es werden keine Einreichungen mehr angenommen. Vielen Dank für deine Teilnahme!",
                    name=last_message.name)

                state = {
                    "messages": [safe_message]
                }
                if not self.continue_graph_execution:
                    state["jump_to"] = "end"
                return state
            return None
        except Exception as e:
            logger.error(f"Error in abefore_model guardrail: {e}", exc_info=True)
            if self.allow_error:
                return None
            return {
                "messages":
                    [AIMessage(content="I cannot process that request because there was an error. Please retry later.")]
            }

    async def awrap_tool_call(self,
                              request: ToolCallRequest,
                              handler: Callable[[ToolCallRequest], ToolMessage | Command]) -> ToolMessage | Command:
        """
        Check if the tool call output is unsafe and block it if it is by replacing it with a new
        tool message with a blocked content.

        NOTE: Assumes that our tools always return ToolMessage and no Command.
        """
        try:
            # Always call the handler first to get the result
            result = await handler(request)

            if not self.wrap_tool_call or result.status != "success":
                return result
            # allow internal users to still access app
            user = request.runtime.context.get("user", {})
            if user and any([user.identifier.endswith(f"@{domain}") for domain in self.whitelisted_domains]):
                return result


            # Check if result is a ToolMessage with content to evaluate
            if isinstance(result, ToolMessage) and result.content:
                if await self._output_is_unsafe(result.content):
                    logger.warning(f"Unsafe content detected in tool output: {request.tool.name}")
                    return ToolMessage(
                        tool_call_id=request.tool_call["id"],
                        content="Tool result blocked by safety guardrail."
                    )
            return result
        except Exception as e:
            logger.error(f"Error in awrap_tool_call guardrail: {e}", exc_info=True)
            return ToolMessage(
                content="There was an error evaluating the tool call.",
                tool_call_id=request.tool_call["id"]
            )

    @hook_config(can_jump_to=["end"])
    async def aafter_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        try:
            if not self.after_model:
                return None
            if len(state["messages"]) == 0:
                return None
            # allow internal users to still access app
            user = runtime.context.get("user", {})
            if user and any([user.identifier.endswith(f"@{domain}") for domain in self.whitelisted_domains]):
                return None


            last_message = state["messages"][-1]
            if not isinstance(last_message, AIMessage) or not last_message.content:
                return None

            if await self._output_is_unsafe(last_message.content):
                logger.warning("Unsafe content detected in agent output, blocking response")
                logger.debug(f"UNSAFE MESSAGE: {last_message.content}")
                safe_message = AIMessage(
                    content="Die Challenge ist beendet. Es werden keine Einreichungen mehr angenommen. Vielen Dank für deine Teilnahme!",
                    name=last_message.name)
                state = {
                    "messages": [RemoveMessage(id=last_message.id), safe_message],
                    "jump_to": "end"
                }
                if not self.continue_graph_execution:
                    state["jump_to"] = "end"
                return state
            return None
        except Exception as e:
            logger.error(f"Error in abefore_model guardrail: {e}", exc_info=True)
            if self.allow_error:
                return None
            return {
                "messages":
                    [AIMessage(content="I cannot process that request because there was an error. Please retry later.")]
            }

    async def _output_is_unsafe(self, content: str) -> bool:
        return True
