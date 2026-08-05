import re
import unicodedata
from typing import Any, Callable, override

import structlog
from langchain.agents.middleware import AgentMiddleware, AgentState, hook_config
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.runtime import Runtime
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.types import Command
from structlog import BoundLogger

from silverspeak import normalize_text
from emoji import demojize

from _old.challenge.guardrails.tracked_agent_guardrail import TrackedAgentMiddleware

logger: BoundLogger = structlog.get_logger("text_sanitizer_guardrail")


class TrackedTextSanitizerGuardrailMiddleware(TrackedAgentMiddleware):
    """
    Text Sanitizer guardrail: Sanitize text input against all sorts of character-based attacks.
    See this paper: https://arxiv.org/html/2504.11168v1

    Args:
        after_model: Whether to apply the guardrail on the model output.
        wrap_tool_call: Whether to apply the guardrail when wrapping tool calls.
        before_model: Whether to apply the guardrail before the model.
        allow_error: Whether to allow the guardrail to fail.
    """
    guardrail_name = "TextSanitizerGuardrail"

    before_model_can_jump_to = ["end"]
    after_model_can_jump_to = ["end"]


    def __init__(self, after_model: bool = False, wrap_tool_call: bool = False, before_model: bool = False,
                 allow_error: bool = False):
        super().__init__()
        if not after_model and not wrap_tool_call and not before_model:
            raise ValueError(
                "One of input parameters after_model, wrap_tool_call, before_model must be True. "
                "Otherwise, middleware has no effect."
            )

        self.before_model = before_model
        self.after_model = after_model
        self.wrap_tool_call = wrap_tool_call

        self.allow_error = allow_error

    @override
    async def _abefore_model(self, state: AgentState, runtime: Runtime):
        """
        Sanitize last user input by sanitizing the input string.
        """
        try:
            if not self.before_model:
                return None
            if len(state["messages"]) == 0:
                return None

            last_message = state["messages"][-1]
            if last_message.type != "human" or not last_message.content:
                return None

            sanitized_content = self._sanitize_text(last_message.content)
            last_message.content = sanitized_content
            new_state = {
                "messages":
                    state["messages"][:-1] + [
                        last_message
                    ]
            }
            return new_state, "sanitized"
        except Exception as e:
            logger.error(f"Error in abefore_model guardrail: {e}", exc_info=True)

            if self.allow_error:
                return None
            new_state = {
                "messages":
                    [AIMessage(content="I cannot process that request because there was an error. Please retry later.")]
            }
            return new_state, "blocked", f"Blocked due to error in abefore_model guardrail: {e}"

    @override
    async def _awrap_tool_call(self,
                              request: ToolCallRequest,
                              handler: Callable[[ToolCallRequest], ToolMessage | Command]):
        """
        Sanitize tool call output by sanitizing the output string.
        """
        try:
            # Always call the handler first to get the result
            message = await handler(request)
            if not self.wrap_tool_call:
                return message, None

            # Check if result is a ToolMessage with content to evaluate
            if isinstance(message, ToolMessage) and message.content:
                sanitized_content = self._sanitize_text(message.content)
                message.content = sanitized_content
                logger.debug(f"Sanitized tool call output: {sanitized_content}")

                return message, "sanitized"
            return message, "passed"
        except Exception as e:
            logger.error(f"Error in awrap_tool_call guardrail: {e}", exc_info=True)
            new_tool_message = ToolMessage(
                tool_call_id=request.tool_call["id"],
                content="There was an error evaluating the tool call.",
            )
            return new_tool_message, "blocked", f"Blocked due to error in awrap_tool_call guardrail: {e}"

    @override
    async def _aafter_model(self, state: AgentState, runtime: Runtime):
        try:
            if not self.after_model:
                return None
            if len(state["messages"]) == 0:
                return None

            last_message = state["messages"][-1]
            if not isinstance(last_message, AIMessage) or not last_message.content:
                return None

            sanitized_content = self._sanitize_text(last_message.content)
            last_message.content = sanitized_content
            new_state = {
                "messages":
                    state["messages"][:-1] + [
                        last_message
                    ]
            }
            return new_state, "sanitized"
        except Exception as e:
            logger.error(f"Error in abefore_model guardrail: {e}", exc_info=True)
            if self.allow_error:
                return None
            new_state = {
                "messages":
                    [AIMessage(content="I cannot process that request because there was an error. Please retry later.")]
            }
            return new_state, "blocked", f"Blocked due to error in abefore_model guardrail: {e}"

    def _sanitize_text(self, text: str) -> str:
        # 1) Normalize to NFC first
        # composes unicode sequences into canonical form
        # e.g., cafeU+0301 => café
        #
        text = unicodedata.normalize('NFC', text)
        # 2) normalize using silverspeak
        # default strategy is DOMINANT_SCRIPT, i.e., the characters are replaced by the dominating charset
        # if the majority of chars is russian. It is replaced with russian
        # not optimal, there is a better approach using
        # SPELL_CHECK: Normalize using spelling correction algorithms with multilingual support
        # but it does not seem to be implemented yet
        # ToDo check again at a later stage
        text = normalize_text(text)
        # 2) remove emojis
        # Here is a list of all emojis: https://unicode.org/emoji/charts/full-emoji-list.html
        text = demojize(text)
        # 3) Remove zero-width chars
        text = re.sub(r'[\u200B\u200C\u200D\uFEFF]', '', text)
        # 4) Collapse single-letter spacing
        text = re.sub(r'\b(?:[A-Za-z]\s+)+[A-Za-z]\b',
                      lambda m: m.group(0).replace(" ", ""), text)
        # 5) Remove Unicode tag characters (U+E0000 to U+E007F)
        # U+E0001, U+E0020–U+E007F were originally intended for invisibly tagging texts by language[3] but that use
        # is no longer recommended.[4] All of those characters were deprecated in Unicode 5.1.
        # https://en.wikipedia.org/wiki/Tags_(Unicode_block)
        text = re.sub(r'[\U000E0000-\U000E007F]', '', text)
        # Remove directional formatting characters (RTL attacks)
        #     # RLO, LRO, RLE, LRE, PDF, RLM, LRM, ALM
        text = re.sub(r'[\u202A-\u202E\u200E\u200F\u061C]', '', text)
        # 6) Remove combining diacritical marks (underlines, overlines, etc.)
        #     # Range U+0300-U+036F (Combining Diacritical Marks)
        #     # Range U+1AB0-U+1AFF (Combining Diacritical Marks Extended)
        #     # Range U+1DC0-U+1DFF (Combining Diacritical Marks Supplement)
        #     # Range U+20D0-U+20FF (Combining Diacritical Marks for Symbols)
        #     # Range U+FE20-U+FE2F (Combining Half Marks)
        text = re.sub(r'[\u0300-\u036F\u1AB0-\u1AFF\u1DC0-\u1DFF\u20D0-\u20FF\uFE20-\uFE2F]', '', text)
        # 7) Convert full-width to half-width characters explicitly
        #
        fullwidth_to_halfwidth = str.maketrans(
            'ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ０１２３４５６７８９',
            'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789'
        )
        text = text.translate(fullwidth_to_halfwidth)
        # 8) Remove dangerous control characters (excluding common whitespace)
        text = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F-\x9F]', '', text)
        # 9) Normalize whitespace (collapse multiple spaces/newlines)
        text = re.sub(r'[ \t]+', ' ', text)  # Collapse spaces/tabs
        text = re.sub(r'\n{3,}', '\n\n', text)  # Limit consecutive newlines

        # 10) decode Leet Speak
        # ToDo find a good way to decode Leet Speak
        # maybe this: https://github.com/Monotoba/leetspeak-encoder-decoder

        return text
