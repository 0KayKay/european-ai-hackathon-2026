"""Case events: the recorded, replayable trace of one case (IMPLEMENTATION.md §3).

Events carry only what the engine puts into `data` (messages, guardrail decisions, tool calls and results, the
success check). Request headers and credentials never reach them (R-EXE-8).
"""

import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Literal

import structlog
from pydantic import BaseModel, Field
from pydantic_core import to_jsonable_python

logger = structlog.get_logger(__name__)

EventKind = Literal[
    "attacker_message",
    "injection",
    "user_task",
    "guardrail",
    "model_call",
    "tool_call",
    "tool_result",
    "agent_reply",
    "success_check",
    "limit",
    "error",
]


class CaseEvent(BaseModel):
    seq: int
    t: float  # seconds since case start
    at: str  # ISO 8601, UTC, milliseconds
    turn: int
    kind: EventKind
    node: str | None = None  # graph node id (hackathon.arena.graph)
    edge: str | None = None  # graph edge id
    data: dict[str, Any] = Field(default_factory=dict)


EventListener = Callable[[CaseEvent], Awaitable[None]]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class EventRecorder:
    """Collects the events of one case and forwards each one to an optional async listener (live sample)."""

    def __init__(self, listener: EventListener | None = None) -> None:
        self.events: list[CaseEvent] = []
        self.turn = 0
        self.listener = listener
        self._start = time.monotonic()

    async def emit(
        self,
        kind: EventKind,
        *,
        node: str | None = None,
        edge: str | None = None,
        **data: Any,
    ) -> CaseEvent:
        event = CaseEvent(
            seq=len(self.events),
            t=round(time.monotonic() - self._start, 3),
            at=_now(),
            turn=self.turn,
            kind=kind,
            node=node,
            edge=edge,
            data=to_jsonable_python(data, fallback=str),
        )
        self.events.append(event)
        if self.listener is not None:
            try:
                await self.listener(event)
            except Exception:
                # A failing live view must never change the outcome of a case.
                logger.exception("Case event listener failed", kind=kind)
        return event
