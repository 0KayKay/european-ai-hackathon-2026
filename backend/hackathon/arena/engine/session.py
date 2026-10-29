"""Execute one case of a resolved level (IMPLEMENTATION.md §3).

`CaseSession` is the API for both drivers:

- direct levels: `await session.turn(message)` per attacker (or benign user) message; every turn is one agent run
  in the same conversation (history kept), with the run limits per turn and the conversation limits across turns;
- indirect levels: `await session.run_indirect(content)` once; the content goes to the injection point and the
  level's fixed user task is the user message;

then `await session.finish()` returns the `CaseResult` (outcome, deciding step, events, usage). `run_case()` does
all of it for a case whose input is known up front (stored datasets, green cases).
"""

import asyncio
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import uuid4

import structlog
from pydantic import BaseModel, Field
from pydantic_ai import Agent, Tool, capture_run_messages
from pydantic_ai.exceptions import UnexpectedModelBehavior, UsageLimitExceeded
from pydantic_ai.messages import ModelMessage
from pydantic_ai.usage import RunUsage, UsageLimits

from hackathon.arena.config.resolved import (
    InjectionError,
    ResolvedGuardrail,
    ResolvedLevel,
)
from hackathon.arena.engine.events import CaseEvent, EventListener, EventRecorder
from hackathon.arena.engine.guardrails import ArenaGuardrails, LimitExceeded
from hackathon.arena.engine.models import (
    EngineModels,
    ModelIdentity,
    case_model_roles,
    correlation_id,
)
from hackathon.arena.registry.guardrails import (
    AGENT_TO_OUTPUT,
    AROUND,
    JUDGE_PENDING_REASON,
    JUDGE_UNAVAILABLE_REASON,
    JUDGE_UNAVAILABLE_RULE,
    SANITIZER_RULE,
    TOOL_TO_AGENT,
    USER_TO_AGENT,
    sanitizer_reason,
)
from hackathon.arena.registry.secrets import SecretContext
from hackathon.arena.registry.success import SUCCESS_KINDS, Observation, SuccessResult
from hackathon.arena.registry.tools import BoundTool, ToolState

logger = structlog.get_logger(__name__)

JUDGE_UNAVAILABLE_ALLOWED_REASON = (
    "The security check could not complete. The action was allowed."
)

Team = Literal["red", "green"]
Outcome = Literal["succeeded", "defended", "passed", "false_positive", "error"]


class Case(BaseModel):
    """Identity of one red attempt or green sample. Its input is passed to `turn()` / `run_indirect()`."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    team: Team
    attack_type: str | None = None


class CaseUsage(BaseModel):
    model_requests: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


class CaseResult(BaseModel):
    case_id: str
    team: Team
    attack_type: str | None
    outcome: Outcome
    # {kind: guardrail, guardrail_id} | {kind: success_check} | {kind: agent} | {kind: turn_limit}
    # | {kind: limit, limit} | {kind: error}
    decided_by: dict[str, Any]
    turns: int
    success: dict[str, Any]  # {met, kind, detail}
    events: list[CaseEvent]
    usage: CaseUsage
    models: list[
        dict[str, Any]
    ]  # LLM roles used: {role, node, model, endpoint, params}
    error: str | None = None


@dataclass
class TurnResult:
    turn: int
    reply: str
    stop: bool  # success check met, a limit was hit or the case failed
    success: bool  # success check met (in this or an earlier turn)
    blocked: list[str] = field(
        default_factory=list
    )  # guardrails that blocked in this turn
    limit: str | None = None
    error: str | None = None


@dataclass
class RunState:
    """Counters and flags of one agent run (one turn)."""

    user_context: str = ""
    input_checked: bool = False
    model_requests: int = 0
    tool_calls: int = 0


@dataclass
class Totals:
    model_requests: int = 0
    tool_calls: int = 0


def _limit_name(error: UsageLimitExceeded) -> str:
    if isinstance(error, LimitExceeded):
        return error.limit
    message = str(error)
    if "request_limit" in message:
        return "run.model_requests"
    if "tool_calls_limit" in message:
        return "run.tool_calls"
    return "run.usage"


class CaseSession:
    def __init__(
        self,
        level: ResolvedLevel,
        case: Case,
        *,
        user_id: str = "arena",
        user_email: str = "",
        secret: str | None = None,
        models: EngineModels | None = None,
        listener: EventListener | None = None,
    ) -> None:
        self.level = level
        self.case = case
        self.models = models or EngineModels()
        self.identity = ModelIdentity(
            user_id=user_id, case_id=case.id, correlation_id=correlation_id()
        )
        self.recorder = EventRecorder(listener)
        self.secret = (
            secret
            if secret is not None
            else level.generate_secret(
                SecretContext(
                    user_id=user_id, thread_id=case.id, difficulty=level.difficulty
                )
            )
        )
        self.tool_state = ToolState(
            variables={"secret": self.secret, "user_email": user_email or user_id}
        )
        self.observes = SUCCESS_KINDS[level.success.kind].observes
        self.tools: dict[str, BoundTool] = {}
        self.judge = self.models.judge(self.identity)
        self._agent: Agent | None = None

        self.run = RunState()
        self.conversation = Totals()
        self.usage = CaseUsage()
        self.history: list[ModelMessage] = []  # conversation the next turn continues
        self.transcript: list[ModelMessage] = []  # every message, including failed runs
        self.turns = 0
        self.blocks: list[str] = []  # guardrail ids in blocking order
        self.success: SuccessResult | None = None  # first met check
        self.last_check: SuccessResult | None = None
        self.limit: str | None = None
        self.exhausted = False  # no turn left (single run done or max_turns reached)
        self.turn_limit = False  # a multi-turn case used all its turns
        self.error: str | None = None
        self.started = False
        self.finished: CaseResult | None = None

    # --- public API ------------------------------------------------------------------------------------------

    @property
    def stopped(self) -> bool:
        """No further turn will run: success met, a limit hit, all turns used or the case failed."""
        return bool(self.success or self.limit or self.exhausted or self.error)

    @property
    def events(self) -> list[CaseEvent]:
        return self.recorder.events

    @property
    def user_context(self) -> str:
        """Trusted user request for tool authorization: the user task (indirect) or this turn's message."""
        return self.level.surface.user_task or self.run.user_context

    async def turn(self, message: str) -> TurnResult:
        """One attacker turn of a direct level: one agent run in the same conversation."""
        if self.level.surface.channel != "direct":
            raise RuntimeError("turn() is for direct levels; use run_indirect()")
        if self.stopped or self.finished:
            return self._turn_result("", [])
        self.started = True
        self.turns += 1
        self.recorder.turn = self.turns
        await self.emit(
            "attacker_message",
            node="attacker",
            edge=self._edge_out("attacker", USER_TO_AGENT, "agent"),
            text=message,
            team=self.case.team,
        )
        blocks_before = len(self.blocks)
        reply = await self._run(message)
        await self._success_event()
        surface = self.level.surface
        if not self.stopped and self.turns >= surface.max_turns:
            self.exhausted = True
            if surface.turns == "multi":
                self.turn_limit = True
                await self.emit(
                    "limit",
                    node="output",
                    limit="max_turns",
                    message=f"Maximum number of turns ({surface.max_turns}) reached.",
                )
        return self._turn_result(reply, self.blocks[blocks_before:])

    async def run_indirect(self, content: dict[str, Any]) -> TurnResult:
        """The single run of an indirect level: `content` at the injection point, the user task as message."""
        surface = self.level.surface
        if surface.channel != "indirect":
            raise RuntimeError("run_indirect() is for indirect levels; use turn()")
        if self.started or self.finished:
            raise RuntimeError("an indirect case runs once")
        self.started = True
        self.turns = 1
        self.recorder.turn = 1
        try:
            injection = self.level.injection_content(content)
        except InjectionError as exc:
            await self._fail(f"Invalid injection content: {exc}")
            return self._turn_result("", [])
        await self.emit(
            "injection",
            node="injection",
            edge="attacker->injection",
            **injection,  # data = exactly the injection fields (IMPLEMENTATION.md §3, §5 WP5)
        )
        await self.emit(
            "user_task",
            node="user_task",
            edge=self._edge_out("user_task", USER_TO_AGENT, "agent"),
            text=surface.user_task,
        )
        self._build_agent(injection)
        reply = await self._run(surface.user_task)
        await self._success_event()
        self.exhausted = True
        return self._turn_result(reply, list(self.blocks))

    async def finish(self, *, error: str | None = None) -> CaseResult:
        """Decide the case. `error` reports a failure of the driver (e.g. the attacker) as a case error."""
        if self.finished:
            return self.finished
        if error and not self.error:
            await self._fail(error)
        if not self.started and not self.error:
            await self._fail("The case was not executed.")
        outcome, decided_by = self._decide()
        success = self.success or self.last_check
        self.finished = CaseResult(
            case_id=self.case.id,
            team=self.case.team,
            attack_type=self.case.attack_type,
            outcome=outcome,
            decided_by=decided_by,
            turns=self.turns,
            success={
                "met": self.success is not None,
                "kind": self.level.success.kind,
                "detail": success.detail if success else {},
            },
            events=list(self.events),
            usage=self.usage,
            models=case_model_roles(self.level),
            error=self.error,
        )
        return self.finished

    # --- run ---------------------------------------------------------------------------------------------------

    def _build_agent(self, injection: dict[str, str] | None = None) -> Agent:
        if self._agent is None:
            tools = self.level.build_tools(self.tool_state, injection)
            self.tools = {tool.name: tool for tool in tools}
            self._agent = Agent(
                model=self.models.agent(self.level.agent.model, self.identity),
                name=self.level.agent.name,
                instructions=self.level.agent.system_prompt,
                tools=[
                    Tool(
                        tool.function,
                        name=tool.name,
                        description=tool.description,
                        sequential=tool.sequential,
                    )
                    for tool in tools
                ],
                capabilities=[ArenaGuardrails(self)],
            )
        return self._agent

    async def _run(self, prompt: str) -> str:
        agent = self._build_agent()
        limits = self.level.limits
        self.run = RunState(user_context=prompt)
        usage = RunUsage()
        result = None
        captured: list[ModelMessage] = []
        try:
            with capture_run_messages() as captured:
                result = await asyncio.wait_for(
                    agent.run(
                        prompt,
                        message_history=self.history,
                        usage=usage,
                        usage_limits=UsageLimits(
                            request_limit=limits.run.model_requests,
                            tool_calls_limit=limits.run.tool_calls,
                        ),
                    ),
                    timeout=limits.timeout_seconds,
                )
        except UsageLimitExceeded as exc:
            self.limit = _limit_name(exc)
            await self.emit("limit", node="agent", limit=self.limit, message=str(exc))
        except TimeoutError:
            await self._fail(
                f"The run exceeded the time limit ({limits.timeout_seconds:g} s)."
            )
        except UnexpectedModelBehavior as exc:
            logger.exception("Arena case model response failed", case_id=self.case.id)
            await self._fail(
                f"The run could not finish (UnexpectedModelBehavior: {exc.message[:300]})."
            )
        except Exception as exc:
            logger.exception("Arena case run failed", case_id=self.case.id)
            await self._fail(f"The run could not finish ({type(exc).__name__}).")
        finally:
            self.conversation.model_requests += self.run.model_requests
            self.conversation.tool_calls += self.run.tool_calls
            self.usage.model_requests += self.run.model_requests
            self.usage.tool_calls += self.run.tool_calls
            self.usage.input_tokens += usage.input_tokens
            self.usage.output_tokens += usage.output_tokens
        if result is None:
            self.transcript += captured[len(self.history) :]
            return ""
        self.transcript += result.new_messages()
        self.history = result.all_messages()
        reply = result.output
        await self.emit(
            "agent_reply",
            node="output",
            edge=self._edge_in("agent", AGENT_TO_OUTPUT, "output"),
            text=reply,
        )
        return reply

    # --- hooks used by ArenaGuardrails -------------------------------------------------------------------

    async def emit(self, kind, /, **values) -> CaseEvent:
        # `kind` is positional-only, so event data may carry its own `kind` (success_check).
        return await self.recorder.emit(kind, **values)

    async def model_call(self) -> None:
        first = self.run.model_requests == 1
        source = "attacker" if self.level.surface.channel == "direct" else "user_task"
        await self.emit(
            "model_call",
            node="agent",
            edge=self._edge_in(source, USER_TO_AGENT, "agent") if first else None,
            model=self.level.agent.model.model,
            n=self.conversation.model_requests + self.run.model_requests,
        )

    async def tool_call(self, name: str, call_id: str, args: dict[str, Any]) -> None:
        await self.emit(
            "tool_call",
            node=f"tool:{name}",
            edge=self._edge_in("agent", f"{AROUND}{name}", f"tool:{name}"),
            tool=name,
            call_id=call_id,
            args=args,
        )

    async def tool_result(
        self, name: str, call_id: str, result: Any, *, blocked_by: str | None
    ) -> None:
        await self.emit(
            "tool_result",
            node=f"tool:{name}",
            edge=self._edge_in(f"tool:{name}", TOOL_TO_AGENT, "agent"),
            tool=name,
            call_id=call_id,
            result=result,
            blocked_by=blocked_by,
        )

    def _guardrail_data(self, guardrail: ResolvedGuardrail) -> dict[str, Any]:
        return {
            "guardrail_id": guardrail.id,
            "guardrail_kind": guardrail.kind,
            "guardrail_name": guardrail.name,
            "position": guardrail.at,
            "model": guardrail.model.model if guardrail.model else None,
        }

    async def sanitized(self, guardrail: ResolvedGuardrail, changed: bool) -> None:
        await self.emit(
            "guardrail",
            node=f"guardrail:{guardrail.id}",
            **self._guardrail_data(guardrail),
            check_id=str(uuid4()),
            status="sanitized",
            rule=SANITIZER_RULE,
            reason=sanitizer_reason(changed),
            changed=changed,
        )

    async def judged(self, guardrail: ResolvedGuardrail, prompt: str) -> bool:
        """Run one judge; True if the action may proceed. A timeout or failure follows `on_error`."""
        check = {
            "node": f"guardrail:{guardrail.id}",
            **self._guardrail_data(guardrail),
            "check_id": str(uuid4()),
            "changed": None,
        }
        await self.emit(
            "guardrail", **check, status="pending", rule="", reason=JUDGE_PENDING_REASON
        )
        try:
            async with asyncio.timeout(guardrail.timeout_seconds):
                decision = await self.judge(guardrail, prompt)
            safe = decision.decision == "SAFE"
            rule, reason, failed = decision.rule, decision.reason, False
        except Exception:
            safe = guardrail.on_error == "allow"
            rule, failed = JUDGE_UNAVAILABLE_RULE, True
            reason = (
                JUDGE_UNAVAILABLE_ALLOWED_REASON if safe else JUDGE_UNAVAILABLE_REASON
            )
        await self.emit(
            "guardrail",
            **check,
            status="passed" if safe else "blocked",
            rule=rule,
            reason=reason,
            failed=failed,
        )
        if not safe:
            self.blocks.append(guardrail.id)
        return safe

    def observe(self, text: str = "") -> bool:
        """Evaluate the success check on one observation; remembers the first met result."""
        result = self.level.check_success(
            Observation(
                secret=self.secret, text=text, sent_emails=self.tool_state.sent_emails
            )
        )
        self.last_check = result
        if result.met and self.success is None:
            self.success = result
        return result.met

    # --- helpers ---------------------------------------------------------------------------------------------

    def _edge_out(self, source: str, position: str, target: str) -> str:
        """First edge of source -> target through the guardrail group at `position` (as in the graph)."""
        if self.level.guardrails_at(position):
            return f"{source}->guardrails:{position}"
        return f"{source}->{target}"

    def _edge_in(self, source: str, position: str, target: str) -> str:
        """Last edge of source -> target through the guardrail group at `position`."""
        if self.level.guardrails_at(position):
            return f"guardrails:{position}->{target}"
        return f"{source}->{target}"

    async def _success_event(self) -> None:
        if self.success is None and self.last_check is None:
            self.observe()
        result = self.success or self.last_check
        observes_outbox = self.observes == "sent_emails"
        outbox = next(
            (t.id for t in self.level.agent.toolsets if t.kind == "send_email"), None
        )
        await self.emit(
            "success_check",
            node="success",
            edge=f"data:{outbox}->success"
            if observes_outbox and outbox
            else "output->success",
            met=self.success is not None,
            kind=result.kind,
            detail=result.detail,
        )

    async def _fail(self, message: str) -> None:
        self.error = message
        await self.emit("error", node=None, message=message)

    def _turn_result(self, reply: str, blocked: list[str]) -> TurnResult:
        return TurnResult(
            turn=self.turns,
            reply=reply,
            stop=self.stopped,
            success=self.success is not None,
            blocked=blocked,
            limit=self.limit or ("max_turns" if self.turn_limit else None),
            error=self.error,
        )

    def _decide(self) -> tuple[Outcome, dict[str, Any]]:
        blocked = (
            {"kind": "guardrail", "guardrail_id": self.blocks[0]}
            if self.blocks
            else None
        )
        if self.case.team == "red":
            if self.success is not None:
                return "succeeded", {"kind": "success_check"}
            if self.error:
                return "error", {"kind": "error"}
            outcome: Outcome = "defended"
        else:
            if self.error:
                return "error", {"kind": "error"}
            if blocked:
                return "false_positive", blocked
            outcome = "passed"
        if blocked:
            return outcome, blocked
        if self.limit:
            return outcome, {"kind": "limit", "limit": self.limit}
        if self.turn_limit:
            return outcome, {"kind": "turn_limit"}
        return outcome, {"kind": "agent"}


@dataclass
class CaseInput:
    """A case with its full input: `messages` (direct, one per turn) or `content` (indirect)."""

    case: Case
    messages: list[str] | None = None
    content: dict[str, Any] | None = None


async def run_case(
    level: ResolvedLevel, case_input: CaseInput, **session_options: Any
) -> CaseResult:
    """Run a case whose input is known up front (single-turn red cases, green cases)."""
    session = CaseSession(level, case_input.case, **session_options)
    if level.surface.channel == "indirect":
        if case_input.content is None:
            return await session.finish(error="An indirect case needs content.")
        await session.run_indirect(case_input.content)
    else:
        if not case_input.messages:
            return await session.finish(error="A direct case needs messages.")
        for message in case_input.messages:
            if (await session.turn(message)).stop:
                break
    return await session.finish()
