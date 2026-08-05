import structlog
from langchain.agents.middleware import AgentState
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.runtime import Runtime
from langchain_core.messages import AIMessage, ToolMessage, HumanMessage, RemoveMessage
from langchain_core.prompts import PromptTemplate
from langgraph.types import Command
from structlog import BoundLogger

from challenge.agents.models import get_gemini_chat_model
from typing import Callable, override

from challenge.guardrails.tracked_agent_guardrail import TrackedAgentMiddleware

logger: BoundLogger = structlog.get_logger("basic_llm_judge_guardrail")


class TrackedBasicLLMJudgeGuardrailMiddleware(TrackedAgentMiddleware):
    """
    Model-based guardrail: Use an LLM to evaluate input/response safety.

    Args:
        safety_prompt_str: The safety prompt to use for evaluating response safety.
                           Template must include placeholder {content}.
        unsafe_word: The word to look for in the response to determine if it is unsafe.
        after_model: Whether to apply the guardrail on the model output.
        wrap_tool_call: Whether to apply the guardrail when wrapping tool calls.
        before_model: Whether to apply the guardrail before the model.
        continue_graph_execution: Whether to continue the agent execution if the guardrail fails. If False, jumps to end.
    """
    guardrail_name = "BasicLLMJudgeGuardrail"

    before_model_can_jump_to = ["end"]
    after_model_can_jump_to = ["end"]


    def __init__(self, safety_prompt_str: str, unsafe_word: str,
                 after_model: bool = False, wrap_tool_call: bool = False, before_model: bool = False,
                 allow_error: bool = False, continue_graph_execution: bool = True):
        super().__init__()
        if not "{content}" in safety_prompt_str:
            raise ValueError("safety_prompt must contain placeholder: {content}.")
        if not after_model and not wrap_tool_call and not before_model:
            raise ValueError(
                "One of input parameters after_model, wrap_tool_call, before_model must be True. "
                "Otherwise, middleware has no effect"
            )

        self.safety_prompt = PromptTemplate.from_template(safety_prompt_str)
        self.unsafe_word = unsafe_word
        self.safety_model = get_gemini_chat_model()

        self.before_model = before_model
        self.after_model = after_model
        self.wrap_tool_call = wrap_tool_call

        self.allow_error = allow_error
        self.continue_graph_execution = continue_graph_execution

    @override
    async def _abefore_model(self, state: AgentState, runtime: Runtime):
        logger.debug("Run before_model guardrail.")
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
                    content="I cannot provide that response because your request is unsafe. Please rephrase your request.")
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
                content="I cannot provide that response because your request is unsafe. Please rephrase your request.")
            new_state = {
                "messages": [safe_message]
            }
            return new_state, "blocked", f"Blocked due to error in abefore_model guardrail: {e}"

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
                tool_call_id=request.tool_call["id"],
                status="error"
            )
            return new_tool_message, "blocked", f"Blocked due to error in awrap_tool_call guardrail: {e}"

    @override
    async def _aafter_model(self, state: AgentState, runtime: Runtime):
        logger.debug("Run after_model guardrail.")
        try:
            if not self.after_model:
                return None
            if len(state["messages"]) == 0:
                return None

            last_message = state["messages"][-1]
            if not isinstance(last_message, AIMessage) or not last_message.content:
                return None

            if await self._output_is_unsafe(last_message.content):
                logger.warning("Unsafe content detected in agent output, blocking response")
                safe_message = AIMessage(
                    name=last_message.name,
                    content="I cannot provide that response because your request is unsafe. Please rephrase your request.")
                new_state = {
                    "messages": [RemoveMessage(id=last_message.id), safe_message]
                }
                if not self.continue_graph_execution:
                    new_state["jump_to"] = "end"
                return new_state, "blocked"
            return None, "passed"
        except Exception as e:
            logger.error(f"Error in abefore_model guardrail: {e}", exc_info=True)
            if self.allow_error:
                return None
            last_message = state["messages"][-1]
            safe_message = AIMessage(
                name=last_message.name,
                content="I cannot provide that response because your request is unsafe. Please rephrase your request.")
            new_state = {
                "messages": [RemoveMessage(id=last_message.id), safe_message]
            }
            return new_state, "blocked", f"Blocked due to error in aafter_model guardrail: {e}"


    async def _output_is_unsafe(self, content: str) -> bool:
        logger.debug("Call _output_is_unsafe")
        # Use a model to evaluate safety
        formatted_prompt = self.safety_prompt.format(content=content)

        # tag is used by streaming handler to filter guardrail messages
        result = await self.safety_model.ainvoke([HumanMessage(content=formatted_prompt)],
                                                 config={"tags": ["guardrail"]})
        logger.debug(
            f"Result: {result}."
        )
        is_unsafe = self.unsafe_word in result.content
        if is_unsafe:
            logger.debug(f"Content flagged as unsafe: {content[:100]}... | Model Output: {result.content}")
        return is_unsafe
