from collections.abc import AsyncIterator
from typing import Annotated

from ag_ui.core import BaseEvent, TextMessageContentEvent
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
from hackathon.api.challenges import prepare_attempt, run_challenge
from hackathon.config import get_settings

router = APIRouter(prefix=get_settings().API_BASE, dependencies=[Depends(require_user)])


@router.post("/ag-ui")
async def run_agent(
    request: Request,
    user_id: Annotated[str, Depends(require_user)],
    prepare_run: Annotated[AGUIRunPreparer, Depends(get_ag_ui_run_preparer)],
) -> Response:
    try:
        run_input = PlatformAGUIAdapter.build_run_input(await request.body())
        if not run_input.thread_id.strip():
            return JSONResponse(
                status_code=422, content={"detail": "threadId is required"}
            )
        agent, deps = await prepare_run(run_input, user_id)
    except ValidationError as exc:
        return JSONResponse(
            status_code=422,
            content={"detail": exc.errors(include_url=False, include_context=False)},
        )
    deps["user_name"] = getattr(request.state, "user_name", "User")
    deps["user_email"] = getattr(request.state, "user_email", "")
    span = trace.get_current_span()
    span.set_attribute("session.id", run_input.thread_id)
    span.set_attribute("user.id", user_id)
    adapter = PlatformAGUIAdapter(
        agent=agent,
        run_input=run_input,
        accept=request.headers.get("accept", SSE_CONTENT_TYPE),
        manage_system_prompt="server",
    )
    attempt = prompt = None
    if deps["mode"] != "PLAYGROUND":
        attempt, prompt = await prepare_attempt(request, run_input, deps)

    async def traced_stream() -> AsyncIterator[BaseEvent]:
        with using_attributes(session_id=run_input.thread_id, user_id=user_id):
            with trace.get_tracer(__name__).start_as_current_span(
                "ag-ui run",
                attributes={
                    "openinference.span.kind": "CHAIN",
                    "input.value": run_input.model_dump_json(by_alias=True),
                    "input.mime_type": "application/json",
                },
            ) as run_span:
                output = []
                try:
                    events = (
                        run_challenge(agent, deps, attempt, prompt, run_input.run_id)
                        if attempt is not None
                        else adapter.run_stream(deps=deps)
                    )
                    async for event in events:
                        if isinstance(event, TextMessageContentEvent):
                            output.append(event.delta)
                        yield event
                finally:
                    run_span.set_attribute("output.value", "".join(output))

    return adapter.streaming_response(traced_stream())
