import asyncio
import json
from collections.abc import AsyncIterator
from threading import Event
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import AnyHttpUrl, BaseModel, Field, field_validator

from hackathon_deepteam.api.auth import require_user
from hackathon_deepteam.config import get_settings
from hackathon_deepteam.runner import run_deepteam
from hackathon_deepteam.scan_config import (
    DEFAULT_GOAL,
    AttackerConfig,
    ObjectiveConfig,
    ScanConfig,
)

router = APIRouter(prefix="/api/deepteam")

CHALLENGE_ONE_EXAMPLE = {
    "goal": DEFAULT_GOAL,
    "target_url": "http://backend:8000/api/v1/ag-ui",
    "model": "gemini-3.8-flash",
    "difficulty": "HARD",
    "attacker": {"model": "ollama/qwen3:8b"},
    "run": {
        "attacks_per_type": 1,
        "linear_turns": 3,
        "attacks": ["LinearJailbreaking"],
    },
}


class RunRequest(BaseModel):
    goal: str | None = Field(default=None, min_length=1, max_length=500)
    target_url: AnyHttpUrl
    target_headers: dict[str, str] = Field(default_factory=dict, repr=False)
    target_state: dict[str, Any] | None = None
    model: Literal["gemini-3.8-flash"] = "gemini-3.8-flash"
    difficulty: Literal["EASY", "MEDIUM", "HARD"] = "HARD"
    attacker: AttackerConfig = Field(default_factory=AttackerConfig)
    objective: ObjectiveConfig = Field(default_factory=ObjectiveConfig)
    run: ScanConfig = Field(default_factory=ScanConfig)

    @field_validator("target_state")
    @classmethod
    def settings_must_be_an_object(cls, value: dict[str, Any] | None):
        if value is not None and not isinstance(value.get("settings", {}), dict):
            raise ValueError("target_state.settings must be an object")
        return value


@router.get("/config")
async def get_run_config(user_id: str = Depends(require_user)) -> dict[str, Any]:
    defaults = RunRequest(
        target_url="http://backend:8000/api/v1/ag-ui",
        difficulty="HARD",
    )
    return defaults.model_dump(mode="json", exclude={"target_headers", "target_state"})


@router.post("/runs")
async def start_run(
    body: Annotated[
        RunRequest,
        Body(
            openapi_examples={
                "challenge_one": {
                    "summary": "Gemini 3.8 Flash · Challenge 1",
                    "value": CHALLENGE_ONE_EXAMPLE,
                }
            }
        ),
    ],
    request: Request,
    user_id: str = Depends(require_user),
) -> dict[str, Any]:
    settings, headers = prepare_run(body, request)
    return await asyncio.to_thread(run_deepteam, body, settings, headers, user_id)


def prepare_run(body: RunRequest, request: Request):
    settings = get_settings()
    allowed_hosts = {
        host.strip().lower()
        for host in settings.ALLOWED_TARGET_HOSTS.split(",")
        if host.strip()
    }
    if body.target_url.host not in allowed_hosts:
        raise HTTPException(status_code=400, detail="Target host is not allowed")

    headers = {
        key: value
        for key, value in body.target_headers.items()
        if key.lower() != "cookie"
    }
    if body.target_url.host == "backend" and (cookie := request.headers.get("cookie")):
        headers["Cookie"] = cookie
    return settings, headers


@router.post("/runs/stream")
async def stream_run(
    body: RunRequest,
    request: Request,
    user_id: str = Depends(require_user),
) -> StreamingResponse:
    settings, headers = prepare_run(body, request)

    async def events() -> AsyncIterator[str]:
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        loop = asyncio.get_running_loop()
        cancel_event = Event()
        failure_sent = False

        def emit(event: dict[str, Any]) -> None:
            nonlocal failure_sent
            if event.get("type") == "run.failed":
                failure_sent = True
            loop.call_soon_threadsafe(queue.put_nowait, event)

        async def work() -> None:
            try:
                await asyncio.to_thread(
                    run_deepteam, body, settings, headers, user_id, emit, cancel_event
                )
            except Exception:
                if not failure_sent:
                    emit(
                        {
                            "type": "run.failed",
                            "detail": "The evaluation stopped unexpectedly.",
                        }
                    )
            finally:
                await asyncio.sleep(0)
                await queue.put(None)

        task = asyncio.create_task(work())
        try:
            while (event := await queue.get()) is not None:
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        finally:
            cancel_event.set()
            if not task.done():
                task.cancel()

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
