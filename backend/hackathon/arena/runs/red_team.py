"""Red-team source: generates single-turn attack cases and drives multi-turn attacks (IMPLEMENTATION.md §6).

`RedTeamSource` is the narrow interface the orchestrator uses; `DeepTeamClient` speaks the deepteam service's
internal NDJSON API. The backend never authors attack content: it only forwards the level's objective, surface
and model specs and validates what comes back.
"""

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
import structlog

from hackathon.arena.config.resolved import InjectionError, ResolvedLevel
from hackathon.arena.config.schema import ModelSpec
from hackathon.arena.engine.models import endpoint_url
from hackathon.config import get_settings

logger = structlog.get_logger(__name__)


class RedTeamError(Exception):
    """The red-team source failed (unreachable, HTTP error, invalid stream or a reported error)."""


@dataclass(frozen=True)
class TurnCallback:
    """Where the red-team driver sends each attacker message of a multi-turn case.

    `url` and `token` go to a remote driver (deepteam calls the backend's internal endpoint); `call` is the same
    turn handler in process, for drivers that run inside the backend (tests).
    """

    url: str
    token: str
    call: Callable[[str], Awaitable[dict[str, Any]]]  # message -> {reply, stop}


class RedTeamSource(Protocol):
    def generate(
        self, level: ResolvedLevel, *, run_id: str, attacks: list[tuple[str, int]]
    ) -> AsyncIterator[dict[str, Any]]:
        """Single-turn cases `{attack_type, input? | content?}` as they are generated; raises `RedTeamError`."""
        ...

    async def drive(
        self,
        level: ResolvedLevel,
        *,
        case_id: str,
        attack_type: str,
        max_turns: int,
        callback: TurnCallback,
    ) -> None:
        """Run one multi-turn attack through `callback` until done or `stop`; raises `RedTeamError`."""
        ...


def check_generated(
    level: ResolvedLevel, raw: Any
) -> tuple[dict[str, Any], str | None]:
    """Validate one generated single-turn case; returns the case (as far as readable) and a problem or None.

    Direct levels need a non-empty `input` string. Indirect levels need `content` with exactly the injection
    fields, each a string within its `max_length`.
    """
    if not isinstance(raw, dict):
        return {}, "a generated case must be an object"
    case: dict[str, Any] = {"attack_type": raw.get("attack_type")}
    if level.surface.channel == "direct":
        value = raw.get("input")
        case["input"] = value if isinstance(value, str) else None
        if not isinstance(value, str) or not value.strip():
            return case, "a direct case needs a non-empty input message"
        return case, None
    content = raw.get("content")
    if not isinstance(content, dict):
        return case, "an indirect case needs content for the injection point"
    case["content"] = content
    names = [field.name for field in level.surface.injection.fields]
    if missing := [name for name in names if name not in content]:
        return case, f"missing injection field(s): {', '.join(missing)}"
    try:
        case["content"] = level.injection_content(content)
    except InjectionError as exc:
        return case, str(exc)
    return case, None


def resolved_spec(spec: ModelSpec) -> dict[str, Any]:
    """A ModelSpec with its endpoint resolved to a base URL, as the deepteam service expects it."""
    return {
        "model": spec.model,
        "endpoint": endpoint_url(spec.endpoint),
        "params": spec.params.compact(),
    }


def red_team_models(level: ResolvedLevel) -> dict[str, Any]:
    red = level.red_team
    return {
        "attacker": resolved_spec(red.attacker),
        "simulator": resolved_spec(red.simulator),
        "evaluator": resolved_spec(red.evaluator),
    }


def generate_body(
    level: ResolvedLevel, run_id: str, attacks: list[tuple[str, int]]
) -> dict[str, Any]:
    surface = level.surface
    injection: dict[str, Any] = {
        "point": surface.injection.point,
        "description": surface.injection.description,
    }
    if surface.injection.fields:
        injection["fields"] = [
            {"name": field.name, "max_length": field.max_length}
            for field in surface.injection.fields
        ]
    return {
        "run_id": run_id,
        "level_id": level.id,
        "attacks": [{"type": type_, "count": count} for type_, count in attacks],
        "objective": level.red_team.objective.model_dump(),
        "surface": {"channel": surface.channel, "injection": injection},
        "models": red_team_models(level),
    }


def multi_turn_body(
    level: ResolvedLevel,
    case_id: str,
    attack_type: str,
    max_turns: int,
    callback: TurnCallback,
) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "attack_type": attack_type,
        "max_turns": max_turns,
        "objective": level.red_team.objective.model_dump(),
        "models": red_team_models(level),
        "callback": {"url": callback.url, "token": callback.token},
    }


class DeepTeamClient:
    """HTTP client of the deepteam service's `/internal/arena` endpoints (NDJSON streams)."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        read_timeout: float = 900,
    ) -> None:
        settings = get_settings()
        self.base_url = (base_url or settings.DEEPTEAM_URL).rstrip("/")
        self.api_key = api_key if api_key is not None else settings.AGENTGATEWAY_API_KEY
        self.transport = transport
        self.timeout = httpx.Timeout(10, read=read_timeout)

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=self.timeout,
            transport=self.transport,
            trust_env=False,
        )

    async def _stream(
        self, path: str, body: dict[str, Any]
    ) -> AsyncIterator[dict[str, Any]]:
        """NDJSON messages of one request; ends after `done`, raises on `error` or a broken stream."""
        try:
            async with (
                self._client() as client,
                client.stream("POST", path, json=body) as response,
            ):
                if response.status_code >= 400:
                    raise RedTeamError(
                        f"DeepTeam service answered {response.status_code} for {path}."
                    )
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        message = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise RedTeamError(
                            "DeepTeam service sent an invalid stream line."
                        ) from exc
                    kind = message.get("type") if isinstance(message, dict) else None
                    if kind == "error":
                        raise RedTeamError(
                            f"DeepTeam reported an error: {message.get('message') or 'unknown error'}"
                        )
                    yield message
                    if kind == "done":
                        return
        except httpx.TimeoutException as exc:
            raise RedTeamError("DeepTeam service timed out.") from exc
        except httpx.HTTPError as exc:
            raise RedTeamError(
                f"DeepTeam service unreachable at {self.base_url} ({type(exc).__name__})."
            ) from exc
        raise RedTeamError("DeepTeam stream ended without 'done'.")

    async def generate(
        self, level: ResolvedLevel, *, run_id: str, attacks: list[tuple[str, int]]
    ) -> AsyncIterator[dict[str, Any]]:
        async for message in self._stream(
            "/internal/arena/generate", generate_body(level, run_id, attacks)
        ):
            if message.get("type") == "case":
                yield message.get("case")

    async def drive(
        self,
        level: ResolvedLevel,
        *,
        case_id: str,
        attack_type: str,
        max_turns: int,
        callback: TurnCallback,
    ) -> None:
        body = multi_turn_body(level, case_id, attack_type, max_turns, callback)
        async for message in self._stream("/internal/arena/multi-turn", body):
            if message.get("type") == "turn":
                logger.debug("DeepTeam turn", case_id=case_id, turn=message.get("turn"))
