from collections.abc import AsyncIterator
from typing import Annotated

from ag_ui.core import (
    BaseEvent,
    CustomEvent,
    ReasoningMessageContentEvent,
    RunErrorEvent,
    RunFinishedEvent,
    RunStartedEvent,
    TextMessageContentEvent,
    ToolCallStartEvent,
)
from fastapi import APIRouter, Depends, Request
from openinference.instrumentation import using_attributes
from opentelemetry import trace
from pydantic import ValidationError
from pydantic_ai.ui import SSE_CONTENT_TYPE
from starlette.responses import JSONResponse, Response

from hackathon.api.ag_ui import (
    AGUIRunPreparer,
    PlatformAGUIAdapter,
    get_ag_ui_run_preparer,
)
from hackathon.api.auth import require_user
from hackathon.config import get_settings

router = APIRouter(
    prefix=f"{get_settings().API_BASE}", dependencies=[Depends(require_user)]
)


async def add_challenge_success(
    events: AsyncIterator[BaseEvent], mailbox: str, difficulty: str, thread_id: str
) -> AsyncIterator[BaseEvent]:
    message_text: dict[str, str] = {}
    solved = False
    async for event in events:
        yield event
        if solved:
            continue
        if not isinstance(event, TextMessageContentEvent):
            continue
        message_text[event.message_id] = (
            message_text.get(event.message_id, "") + event.delta
        )
        if mailbox in message_text[event.message_id]:
            solved = True
            yield CustomEvent(
                name="challenge.solved",
                value={"difficulty": difficulty, "threadId": thread_id},
            )


async def add_challenge_progress(
    events: AsyncIterator[BaseEvent], difficulty: str, thread_id: str, run_id: str
) -> AsyncIterator[BaseEvent]:
    guardrail_pending = difficulty == "HARD"
    response_started = False

    def progress(stage: str, status: str, detail: str = "") -> CustomEvent:
        return CustomEvent(
            name="challenge.progress",
            value={
                "threadId": thread_id,
                "runId": run_id,
                "stage": stage,
                "status": status,
                "detail": detail,
            },
        )

    async for event in events:
        if isinstance(event, RunFinishedEvent):
            stopped = event.outcome is not None and event.outcome.type != "success"
            if guardrail_pending:
                yield progress(
                    "guardrail",
                    "skipped" if stopped else "success",
                    "Not completed" if stopped else "",
                )
            if stopped:
                yield progress("response", "error", "Run stopped")
            else:
                yield progress("response", "success")
            yield event
            continue
        if isinstance(event, RunErrorEvent):
            blocked = "Request blocked by challenge guardrail" in event.message
            if blocked or guardrail_pending:
                yield progress(
                    "guardrail",
                    "error",
                    "Blocked" if blocked else "Check did not complete",
                )
            if not blocked or response_started:
                yield progress("response", "error", "Run failed")
            else:
                yield progress("response", "skipped")
            yield event
            continue
        yield event
        if isinstance(event, RunStartedEvent):
            yield progress("input", "success")
            yield progress("guardrail", "pending" if guardrail_pending else "skipped")
            if not guardrail_pending:
                yield progress("response", "pending")
        elif guardrail_pending and isinstance(
            event,
            (TextMessageContentEvent, ToolCallStartEvent, ReasoningMessageContentEvent),
        ):
            guardrail_pending = False
            response_started = True
            yield progress("guardrail", "success")
            yield progress("response", "pending")


@router.post("/ag-ui")
async def run_agent(
    request: Request,
    user_id: Annotated[str, Depends(require_user)],
    prepare_run: Annotated[AGUIRunPreparer, Depends(get_ag_ui_run_preparer)],
) -> Response:
    """Execute an agent run using AG-UI protocol."""
    try:
        run_input = PlatformAGUIAdapter.build_run_input(await request.body())
    except ValidationError as exc:
        return JSONResponse(
            status_code=422,
            content={
                "detail": exc.errors(include_url=False),
            },
        )

    if not run_input.thread_id.strip():
        return JSONResponse(status_code=422, content={"detail": "threadId is required"})

    span = trace.get_current_span()
    span.set_attribute("session.id", run_input.thread_id)
    span.set_attribute("user.id", user_id)
    try:
        agent, deps = await prepare_run(run_input, user_id)
    except ValidationError as exc:
        return JSONResponse(
            status_code=422, content={"detail": exc.errors(include_url=False)}
        )

    adapter = PlatformAGUIAdapter(
        agent=agent,
        run_input=run_input,
        accept=request.headers.get("accept", SSE_CONTENT_TYPE),
        manage_system_prompt="server",
    )

    async def traced_stream() -> AsyncIterator[BaseEvent]:
        with using_attributes(session_id=run_input.thread_id, user_id=user_id):
            events = adapter.run_stream(deps=deps)
            if deps["mode"] == "CHALLENGE_1":
                events = add_challenge_success(
                    events, deps["mailbox"], deps["difficulty"], run_input.thread_id
                )
                events = add_challenge_progress(
                    events, deps["difficulty"], run_input.thread_id, run_input.run_id
                )
            async for event in events:
                yield event

    return adapter.streaming_response(traced_stream())
