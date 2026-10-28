"""Guardrails at their configured positions, as a pydantic-ai capability.

Generalizes the legacy `hackathon.agent.guardrails.ChallengeGuardrails`: instead of branching on level and
difficulty, it runs the guardrails of the resolved level at their positions, in their configured order:

- `user->agent`: once per agent run, on the latest user prompt, before the first model request. A sanitizer
  rewrites the prompt; a judge that blocks answers with the refusal text instead of calling the model.
- `agent->output`: on every model response, including intermediate tool-call responses. A sanitizer rewrites the
  text parts (and reports even without text); a judge sees the joined text (only when there is text) and on
  block replaces all parts with the refusal text, which drops pending tool calls.
- `around:<tool>`: before the tool executes. A tool authorization that blocks returns its `blocked_message` as
  the tool result; the tool and the tool-result guardrails are skipped.
- `tool->agent`: on the tool result. A sanitizer rewrites it; a judge that blocks replaces it.

Limits are checked like the legacy capability: model requests per conversation before each model request, tool
calls per run and per conversation before each tool call (on top of pydantic-ai's per-run `UsageLimits`).
Success checks that observe the agent output run on every model response (after the output guardrails); once met,
the response is cut to its text so the run ends, like the legacy levels 1 and 3. Checks that observe the outbox
run after every tool execution.
"""

import json
from typing import TYPE_CHECKING, Any

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import ModelResponse, TextPart, UserPromptPart

from hackathon.arena.config.templates import render
from hackathon.arena.registry.guardrails import (
    AGENT_TO_OUTPUT,
    AROUND,
    TOOL_TO_AGENT,
    USER_TO_AGENT,
    sanitize,
)

if TYPE_CHECKING:
    from hackathon.arena.engine.session import CaseSession

TOOL_RESULT_BLOCKED = "Tool result blocked by security guardrail."


class LimitExceeded(UsageLimitExceeded):
    """A run or conversation limit of the level config; `limit` names it (e.g. `conversation.tool_calls`)."""

    def __init__(self, limit: str, message: str) -> None:
        super().__init__(message)
        self.limit = limit


def latest_user_prompt(messages) -> UserPromptPart | None:
    return next(
        (
            part
            for message in reversed(messages)
            for part in reversed(message.parts)
            if isinstance(part, UserPromptPart)
        ),
        None,
    )


def response_text(response: ModelResponse) -> str:
    return "\n".join(
        part.content for part in response.parts if isinstance(part, TextPart)
    )


def as_text(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


class ArenaGuardrails(AbstractCapability[None]):
    def __init__(self, session: "CaseSession") -> None:
        self.session = session

    async def wrap_model_request(self, ctx, *, request_context, handler):
        session = self.session
        level = session.level
        run = session.run
        refusal = level.refusal.text

        if not run.input_checked and (guardrails := level.guardrails_at(USER_TO_AGENT)):
            latest = latest_user_prompt(request_context.messages)
            if latest is not None and isinstance(latest.content, str):
                run.input_checked = True
                for guardrail in guardrails:
                    if guardrail.kind == "sanitizer":
                        latest.content, changed = sanitize(latest.content)
                        await session.sanitized(guardrail, changed)
                    elif not await session.judged(
                        guardrail,
                        render(guardrail.policy.text, {"content": latest.content}),
                    ):
                        return ModelResponse(parts=[TextPart(refusal)])
                run.user_context = latest.content

        limits = level.limits.conversation
        if (
            session.conversation.model_requests + run.model_requests
            >= limits.model_requests
        ):
            raise LimitExceeded(
                "conversation.model_requests",
                f"Conversation model-request limit ({limits.model_requests}) reached.",
            )
        run.model_requests += 1
        await session.model_call()
        response = await handler(request_context)

        for guardrail in level.guardrails_at(AGENT_TO_OUTPUT):
            if guardrail.kind == "sanitizer":
                changed = False
                for part in response.parts:
                    if isinstance(part, TextPart):
                        part.content, part_changed = sanitize(part.content)
                        changed = changed or part_changed
                await session.sanitized(guardrail, changed)
            elif (text := response_text(response)) and not await session.judged(
                guardrail, render(guardrail.policy.text, {"content": text})
            ):
                response.parts = [TextPart(refusal)]
                break

        text = response_text(response)
        if session.observes == "output" and session.observe(text=text):
            response.parts = [TextPart(text)]
        return response

    async def wrap_tool_execute(self, ctx, *, call, tool_def, args, handler):
        session = self.session
        level = session.level
        run = session.run
        tool = session.tools[call.tool_name]
        await session.tool_call(call.tool_name, call.tool_call_id, args)

        run_limit = level.limits.run.tool_calls
        conversation_limit = level.limits.conversation.tool_calls
        if run.tool_calls >= run_limit:
            raise LimitExceeded(
                "run.tool_calls", f"Run tool-call limit ({run_limit}) reached."
            )
        if session.conversation.tool_calls + run.tool_calls >= conversation_limit:
            raise LimitExceeded(
                "conversation.tool_calls",
                f"Conversation tool-call limit ({conversation_limit}) reached.",
            )
        run.tool_calls += 1

        for guardrail in level.guardrails_at(f"{AROUND}{call.tool_name}"):
            values = {
                "user_context": session.user_context,
                **{
                    f"args.{name}": value
                    for name, value in tool.call_arguments(args).items()
                },
            }
            if not await session.judged(
                guardrail, render(guardrail.policy.text, values)
            ):
                result = guardrail.blocked_message
                await session.tool_result(
                    call.tool_name, call.tool_call_id, result, blocked_by=guardrail.id
                )
                return result

        result = await handler(args)
        if session.observes == "sent_emails":
            session.observe()

        blocked_by = None
        for guardrail in level.guardrails_at(TOOL_TO_AGENT):
            if guardrail.kind == "sanitizer":
                result, changed = sanitize(result)
                await session.sanitized(guardrail, changed)
            elif not await session.judged(
                guardrail, render(guardrail.policy.text, {"content": as_text(result)})
            ):
                result = guardrail.blocked_message or TOOL_RESULT_BLOCKED
                blocked_by = guardrail.id
                break
        await session.tool_result(
            call.tool_name, call.tool_call_id, result, blocked_by=blocked_by
        )
        return result
