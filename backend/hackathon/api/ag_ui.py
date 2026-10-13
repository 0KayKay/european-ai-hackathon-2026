from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import datetime

import structlog
from ag_ui.core import BaseEvent, RunAgentInput
from pydantic_ai import Agent
from pydantic_ai.messages import NativeToolCallPart, NativeToolReturnPart
from pydantic_ai.ui import UIEventStream
from pydantic_ai.ui.ag_ui import AGUIAdapter, AGUIEventStream

from hackathon.agent.builder import build_agent
from hackathon.agent.challenge1 import mailbox_for
from hackathon.agent.dependencies import AgentDependencies
from hackathon.agent.settings import ChatState

logger = structlog.get_logger("ag_ui")


@dataclass
class PlatformAGUIEventStream(AGUIEventStream[AgentDependencies, str]):
    """Add logging and handle Azure OpenAI's unpaired native tool-search IDs."""

    _pending_native_tool_calls: deque[str] = field(default_factory=deque)

    def handle_builtin_tool_call_start(
        self, part: NativeToolCallPart
    ) -> AsyncIterator[BaseEvent]:
        """Track calls because Azure OpenAI can omit their ID from native results."""
        self._pending_native_tool_calls.append(part.tool_call_id)
        return super().handle_builtin_tool_call_start(part)

    async def handle_builtin_tool_return(
        self, part: NativeToolReturnPart
    ) -> AsyncIterator[BaseEvent]:
        """Pair an Azure OpenAI native result with its preceding call before emitting it."""
        result_id = part.tool_call_id
        provider_call_id = (part.provider_details or {}).get("call_id")
        call_id = result_id if result_id in self._builtin_tool_call_ids else None
        if call_id is None and isinstance(provider_call_id, str):
            call_id = (
                provider_call_id
                if provider_call_id in self._builtin_tool_call_ids
                else None
            )
        if call_id is None and self._pending_native_tool_calls:
            call_id = self._pending_native_tool_calls.popleft()

        if call_id is not None:
            if call_id in self._pending_native_tool_calls:
                self._pending_native_tool_calls.remove(call_id)
            if call_id != result_id:
                logger.debug(
                    "AG-UI native tool result did not include its call ID; paired by response order",
                    tool_call_id=call_id,
                    result_id=result_id,
                    tool_kind=part.tool_kind,
                )
                part = replace(part, tool_call_id=call_id)
        else:
            logger.debug(
                "AG-UI native tool result has no matching call",
                result_id=result_id,
                tool_kind=part.tool_kind,
            )
            self._builtin_tool_call_ids[result_id] = result_id

        async for event in super().handle_builtin_tool_return(part):
            yield event

    async def on_error(self, error: Exception) -> AsyncIterator[BaseEvent]:
        """Log the exception that Pydantic AI otherwise converts only to RUN_ERROR."""
        logger.exception(
            "AG-UI stream failed", error_type=type(error).__name__, error=str(error)
        )
        async for event in super().on_error(error):
            yield event


class PlatformAGUIAdapter(AGUIAdapter[AgentDependencies, str]):
    """AG-UI adapter with application logging and native tool-result compatibility."""

    def build_event_stream(
        self,
    ) -> UIEventStream[RunAgentInput, BaseEvent, AgentDependencies, str]:
        """Use the platform event stream implementation."""
        return PlatformAGUIEventStream(
            self.run_input, accept=self.accept, ag_ui_version=self.ag_ui_version
        )


AGUIRunPreparer = Callable[
    [RunAgentInput, str], Awaitable[tuple[Agent[AgentDependencies], AgentDependencies]]
]


async def prepare_ag_ui_run(
    run_input: RunAgentInput,
    user_id: str,
) -> tuple[Agent[AgentDependencies], AgentDependencies]:
    """Prepare endpoint run, similar to playground router"""
    state = ChatState.model_validate(run_input.state)
    settings = state.settings

    deps: AgentDependencies = {
        "user_id": user_id,
        "current_date": datetime.now(),
        "mailbox": mailbox_for(user_id, run_input.thread_id, settings.difficulty)
        if settings.mode == "CHALLENGE_1"
        else "",
        "difficulty": settings.difficulty,
        "mode": settings.mode,
    }

    agent = build_agent(deps, settings.model_id, settings.difficulty, settings.mode)

    return agent, deps


def get_ag_ui_run_preparer() -> AGUIRunPreparer:
    """Get AG-UI compatible agent run"""
    return prepare_ag_ui_run
