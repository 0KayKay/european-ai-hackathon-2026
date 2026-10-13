from functools import wraps

import structlog
from ag_ui.core import ToolMessage
from langchain.agents.middleware import AgentMiddleware, AgentState, hook_config
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.runtime import Runtime
from langchain_core.messages import BaseMessage, RemoveMessage
from typing import Any, Callable, Literal
from datetime import datetime

from langgraph.types import Command

logger = structlog.get_logger("guardrail_tracker")


class TrackedAgentMiddleware(AgentMiddleware):
    """
    Base class for guardrails with automatic tracking.

    Your _check_* methods must return:
    1. (None, status) - no state modification, with status
    2. (None, status, reason) - no state modification, with status and reason
    3. (dict, status) - state modifications with status
    4. (dict, status, reason) - state modifications with status and reason

    Examples:
        # Passed
        return None, "passed"

        # Passed with reason
        return None, "passed", "Content is safe"

        # Blocked with state change
        return {"messages": [AIMessage(content="Blocked")], "jump_to": "end"}, "blocked"

        # Blocked with reason
        return {"messages": [...]}, "blocked", "Prompt injection detected"

        # Sanitized
        return {"messages": [HumanMessage(content=clean)]}, "sanitized", "Profanity removed"
    """

    guardrail_name: str = "UnnamedGuardrail"

    # Override these in your subclass to configure decorators
    before_model_can_jump_to: list[Literal["tools", "model", "end"]] | None | None = None
    after_model_can_jump_to: list[Literal["tools", "model", "end"]] | None | None = None
    wrap_tool_call_jump_to: list[Literal["tools", "model", "end"]] | None | None = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Apply decorators based on subclass configuration
        self._apply_decorators()

    def _apply_decorators(self):
        """Apply hook_config decorators based on class variables"""
        if self.before_model_can_jump_to is not None:
            # Get the original unbound method from the class
            original_method_before = self.__class__.abefore_model

            # Create a wrapper that will be decorated
            @hook_config(can_jump_to=self.before_model_can_jump_to)
            @wraps(original_method_before)
            async def decorated_before_model(state: AgentState, runtime: Runtime):
                return await original_method_before(self, state, runtime)

            # Replace the instance method
            self.abefore_model = decorated_before_model

        if self.after_model_can_jump_to is not None:
            # Get the original unbound method from the class
            original_method_after = self.__class__.aafter_model

            # Create a wrapper that will be decorated
            @hook_config(can_jump_to=self.after_model_can_jump_to)
            @wraps(original_method_after)
            async def decorated_after_model(state: AgentState, runtime: Runtime):
                return await original_method_after(self, state, runtime)

            # Replace the instance method
            self.aafter_model = decorated_after_model

        if self.wrap_tool_call_jump_to is not None:
            # Get the original unbound method from the class
            original_method_wrap_tool = self.__class__.awrap_tool_call

            # Create a wrapper that will be decorated
            @hook_config(can_jump_to=self.wrap_tool_call_jump_to)
            @wraps(original_method_wrap_tool)
            async def decorated_wrap_tool(request: ToolCallRequest, handler: Callable[[ToolCallRequest], ToolMessage | Command]):
                return await original_method_wrap_tool(self, request, handler)

            # Replace the instance method
            self.awrap_tool_call = decorated_wrap_tool

            self.awrap_tool_call = hook_config(
                can_jump_to=self.wrap_tool_call_jump_to
            )(self.awrap_tool_call)

    @staticmethod
    def mark_message_as_safe(message: BaseMessage):
        """
        Mark a message as safe, so it skips all guardrail checks.
        This is useful for messages that are known to be safe by design.

        Args:
            message: The message to mark as safe
        """
        if not hasattr(message, 'additional_kwargs'):
            message.additional_kwargs = {}

        message.additional_kwargs['_safe_by_design'] = True

    @staticmethod
    def is_message_safe_by_design(message: BaseMessage) -> bool:
        """Check if a message is marked as safe by design"""
        if not hasattr(message, 'additional_kwargs'):
            return False

        return message.additional_kwargs.get('_safe_by_design', False)

    def _has_been_checked(self, message: BaseMessage) -> bool:
        """Check if this message has already been checked by this guardrail"""
        if not hasattr(message, 'additional_kwargs'):
            return False

        if 'guardrails' not in message.additional_kwargs:
            return False

        # Check if this guardrail has already annotated this message
        for check in message.additional_kwargs['guardrails']:
            if check.get('name') == self.guardrail_name:
                return True

        return False

    def _should_skip_check(self, message: BaseMessage) -> bool:
        """
        Determine if this message should skip checking.

        Returns True if:
        - Message is marked as safe by design
        - Message has already been checked by this guardrail
        """
        if self.is_message_safe_by_design(message):
            logger.debug(f"Message marked as safe by design, skipping {self.guardrail_name}")
            return True

        if self._has_been_checked(message):
            logger.debug(f"Message already checked by {self.guardrail_name}, skipping")
            return True

        return False

    def _annotate_message(self, message: BaseMessage, status: str, reason: str = ""):
        """Add guardrail tracking info to message metadata"""
        if not hasattr(message, 'additional_kwargs'):
            message.additional_kwargs = {}

        if 'guardrails' not in message.additional_kwargs:
            message.additional_kwargs['guardrails'] = []

        check_info = {
            'name': self.guardrail_name,
            'status': status,
            'reason': reason,
            'timestamp': datetime.now().isoformat()
        }

        message.additional_kwargs['guardrails'].append(check_info)
        logger.debug(f"Annotated message with guardrail info: {check_info}")

    def _parse_check_result(self, result) -> tuple[dict[str, Any] | None, str, str] | None:
        """
        Parse the result from _check_* methods.

        Returns:
            tuple of (state_update, status, reason)
        """
        # If result is None (not a tuple), skip tracking
        if result is None:
            return None

        if not isinstance(result, tuple):
            raise ValueError(
                f"Check method must return tuple of (state_update, status) or "
                f"(state_update, status, reason), got {type(result)}"
            )

        if len(result) == 2:
            state_update, status = result
            reason = ""
        elif len(result) == 3:
            state_update, status, reason = result
        else:
            raise ValueError(
                f"Check method must return tuple with 2 or 3 elements, got {len(result)}"
            )

        return state_update, status, reason

    def _transfer_guardrails_to_replacement(self, state_update: dict[str, Any], original_message: BaseMessage):
        """
        Transfer guardrail annotations from a removed message to its replacement message.

        When a message is removed and replaced (pattern: RemoveMessage followed by new message),
        this transfers the guardrail tracking from the original to the replacement so the
        audit trail is preserved on the actual message in the conversation.

        Args:
            state_update: The state update dictionary that may contain RemoveMessage and replacement
            original_message: The message that was checked and annotated
        """
        if not state_update or "messages" not in state_update:
            return

        messages = state_update["messages"]
        if len(messages) < 2:
            return

        # Find RemoveMessage and its replacement
        remove_idx = None
        replacement_idx = None

        for idx, msg in enumerate(messages):
            is_remove = isinstance(msg, RemoveMessage) or \
                        (hasattr(msg, '__class__') and msg.__class__.__name__ == 'RemoveMessage')

            if is_remove:
                # Check if this RemoveMessage is removing our original message
                remove_id = msg.id if hasattr(msg, 'id') else None
                original_id = original_message.id if hasattr(original_message, 'id') else None

                if remove_id == original_id:
                    remove_idx = idx
                    # Look for the next non-RemoveMessage as the replacement
                    for next_idx in range(idx + 1, len(messages)):
                        next_msg = messages[next_idx]
                        is_next_remove = isinstance(next_msg, RemoveMessage) or \
                                         (hasattr(next_msg,
                                                  '__class__') and next_msg.__class__.__name__ == 'RemoveMessage')
                        if not is_next_remove:
                            replacement_idx = next_idx
                            break
                    break

        # If we found both a remove and a replacement, transfer the annotations
        if remove_idx is not None and replacement_idx is not None:
            replacement_msg = messages[replacement_idx]

            # Get guardrail info from the original message
            if hasattr(original_message, 'additional_kwargs') and \
                    'guardrails' in original_message.additional_kwargs:

                # Initialize additional_kwargs on replacement if needed
                if not hasattr(replacement_msg, 'additional_kwargs'):
                    replacement_msg.additional_kwargs = {}

                # Transfer the guardrails list
                if 'guardrails' not in replacement_msg.additional_kwargs:
                    replacement_msg.additional_kwargs['guardrails'] = []

                # Prepend original message's guardrails (so they appear first in history)
                replacement_msg.additional_kwargs['guardrails'] = \
                    original_message.additional_kwargs['guardrails'] + \
                    replacement_msg.additional_kwargs['guardrails']

                # Also mark that this message replaced another one
                replacement_msg.additional_kwargs['replaced_message'] = {
                    'id': original_message.id if hasattr(original_message, 'id') else None,
                    'type': original_message.__class__.__name__,
                    'replaced_at': datetime.now().isoformat()
                }

                logger.debug(f"Transferred {len(original_message.additional_kwargs['guardrails'])} "
                             f"guardrail checks to replacement message")

    def _track_and_annotate(self, state: AgentState, parsed_result: tuple[dict[str, Any] | None, str, str]):
        """Annotate messages based on the guardrail result"""
        if not state.get("messages"):
            return

        state_update, status, reason = parsed_result

        last_message = state["messages"][-1]

        # Skip if this message should not be checked
        if self._should_skip_check(last_message):
            return

        # Annotate the original message
        self._annotate_message(last_message, status, reason)

        # Transfer guardrail info to replacement message if one exists
        if state_update:
            self._transfer_guardrails_to_replacement(state_update, last_message)

        # Annotate any new messages being added
        if state_update and "messages" in state_update:
            for msg in state_update["messages"]:
                # Skip RemoveMessage objects
                if isinstance(msg, RemoveMessage) or \
                        (hasattr(msg, '__class__') and msg.__class__.__name__ == 'RemoveMessage'):
                    continue

                # Only mark messages as safe by design if they're blocking/error messages
                # Sanitized messages should continue through the guardrail chain
                if status in ("blocked", "error"):
                    self.mark_message_as_safe(msg)

    # Override these in your subclass with your logic
    async def _abefore_model(self, state: AgentState, runtime: Runtime):
        """
        Override this with your guardrail logic.

        Must return:
        - (None, status): pass through with status
        - (None, status, reason): pass through with status and reason
        - (dict, status): state update with status
        - (dict, status, reason): state update with status and reason
        """
        return None, "passed"

    async def _aafter_model(self, state: AgentState, runtime: Runtime):
        """
        Override this with your guardrail logic.

        Must return:
        - (None, status): pass through with status
        - (None, status, reason): pass through with status and reason
        - (dict, status): state update with status
        - (dict, status, reason): state update with status and reason
        """
        return None, "passed"

    async def _awrap_tool_call(self, request, handler: Callable):
        """
        Override this with your guardrail logic.

        Must return:
        - (ToolMessage, status): result with status
        - (ToolMessage, status, reason): result with status and reason
        """
        result = await handler(request)
        return result, "passed"

    # These are the actual hooks (decorators applied dynamically in __init__)
    async def abefore_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        """Wrapper that adds tracking"""
        logger.debug("RUN ABEFORE_MODEL")
        try:
            result = await self._abefore_model(state, runtime)
            parsed = self._parse_check_result(result)

            # If None, skip tracking entirely
            if parsed is None:
                return None

            self._track_and_annotate(state, parsed)
            # Return only the state update part (not the status/reason)
            return parsed[0]
        except Exception as e:
            logger.debug(f"Error in abefore_model: {e}")
            if state.get("messages"):
                self._annotate_message(state["messages"][-1], "error", str(e))
            raise

    async def aafter_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        """Wrapper that adds tracking"""
        try:
            result = await self._aafter_model(state, runtime)
            parsed = self._parse_check_result(result)

            # If None, skip tracking entirely
            if parsed is None:
                return None

            self._track_and_annotate(state, parsed)
            # Return only the state update part (not the status/reason)
            return parsed[0]
        except Exception as e:
            if state.get("messages"):
                self._annotate_message(state["messages"][-1], "error", str(e))
            raise

    async def awrap_tool_call(self, request, handler: Callable) -> Any:
        """Wrapper that adds tracking"""
        try:
            result = await self._awrap_tool_call(request, handler)

            if not isinstance(result, tuple):
                raise ValueError(
                    f"_check_tool_call must return tuple of (ToolMessage, status) where "
                    f"status can be None to skip tracking, got {type(result)}"
                )

            if len(result) == 2:
                tool_result, status = result
                reason = ""
            elif len(result) == 3:
                tool_result, status, reason = result
            else:
                raise ValueError(
                    f"_check_tool_call must return tuple with 2 or 3 elements, got {len(result)}"
                )

            # If status is None, skip tracking
            if status is None:
                return tool_result

            # Check if should skip
            if self._should_skip_check(tool_result):
                return tool_result

            # Check if we're replacing the tool result
            if status == "blocked" and tool_result != result:
                # Mark the blocked replacement as safe
                self.mark_message_as_safe(tool_result)

            # Otherwise, annotate and track
            if hasattr(tool_result, 'content'):
                self._annotate_message(tool_result, status, reason)

            return tool_result
        except Exception as e:
            logger.error(f"Error in tool call guardrail: {e}")
            raise
