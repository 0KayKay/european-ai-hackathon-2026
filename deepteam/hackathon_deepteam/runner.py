"""Run the branch's Level 1 evaluation against the live AG-UI challenge."""

from __future__ import annotations

import asyncio
from threading import Event
from time import perf_counter
from typing import TYPE_CHECKING, Any, Callable
from uuid import uuid4

import structlog
from deepteam import red_team
from deepteam.attacks.multi_turn import LinearJailbreaking
from deepteam.attacks.single_turn import PromptInjection, Roleplay
from deepteam.test_case import RTTurn, ToolCall

from hackathon_deepteam.agui_target import call_agui, thread_id_for
from hackathon_deepteam.config import Settings
from hackathon_deepteam.mailbox_metric import (
    LevelCracked,
    MailboxDisclosure,
    disclosed_mailbox,
    mailbox_for,
)
from hackathon_deepteam.qwen_model import LocalQwenModel

if TYPE_CHECKING:
    from hackathon_deepteam.api.runs import RunRequest

logger = structlog.get_logger("deepteam")


def build_attacks(request: RunRequest, attacker: LocalQwenModel) -> list:
    available = {
        "PromptInjection": lambda: PromptInjection(),
        "Roleplay": lambda: Roleplay(),
        "LinearJailbreaking": lambda: LinearJailbreaking(
            num_turns=request.run.linear_turns, simulator_model=attacker
        ),
    }
    return [available[name]() for name in request.run.attacks]


def run_deepteam(
    request: RunRequest,
    settings: Settings,
    headers: dict[str, str],
    user_id: str,
    emit: Callable[[dict[str, Any]], None] | None = None,
    cancel_event: Event | None = None,
) -> dict[str, Any]:
    run_id = str(uuid4())
    log = logger.bind(run_id=run_id, user_id=user_id)
    started = perf_counter()
    objective = request.objective
    purpose = request.goal or objective.purpose
    log.info(
        "run_started",
        target_host=request.target_url.host,
        target_path=request.target_url.path,
        target_model=request.model,
        attacker_model=request.attacker.model,
        difficulty=request.difficulty,
        attacks=request.run.attacks,
        attacks_per_type=request.run.attacks_per_type,
    )
    state = {**(request.target_state or {})}
    state["settings"] = {
        **state.get("settings", {}),
        "modelId": request.model,
        "mode": "CHALLENGE_1",
        "difficulty": request.difficulty,
    }
    attempt_number = 0
    attempt_lock = asyncio.Lock()
    winning_case: LevelCracked | None = None
    stop_event = cancel_event or Event()
    attacker: LocalQwenModel | None = None
    tool_call_names: dict[str, str] = {}
    responding_threads: set[str] = set()

    def abort_work() -> None:
        stop_event.set()
        abort = getattr(attacker, "abort", None)
        if callable(abort):
            abort()

    def cancel_pending_tasks() -> None:
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            return
        if loop.is_closed() or loop.is_running():
            return
        pending = asyncio.all_tasks(loop)
        if not pending:
            return

        async def drain() -> None:
            for task in pending:
                task.cancel()
            done, remaining = await asyncio.wait(pending, timeout=5)
            if done:
                await asyncio.gather(*done, return_exceptions=True)
            if remaining:
                log.warning("pending_tasks_cancel_timeout", count=len(remaining))

        loop.run_until_complete(drain())

    async def wait_for_agent_or_stop(coroutine):
        task = asyncio.create_task(coroutine)
        try:
            while not task.done():
                await asyncio.wait({task}, timeout=0.1)
                if stop_event.is_set() and not task.done():
                    task.cancel()
                    raise RuntimeError("DeepTeam run stopped after an internal error")
            return await task
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    if emit:
        emit(
            {
                "type": "run.started",
                "run_id": run_id,
                "difficulty": request.difficulty,
                "attacks": request.run.attacks,
            }
        )

    def relay_agent_event(thread_id: str, event: dict[str, Any]) -> None:
        if not emit:
            return
        kind = event.get("type")
        if kind == "TEXT_MESSAGE_CONTENT":
            if thread_id not in responding_threads:
                responding_threads.add(thread_id)
                emit({"type": "agent.responding", "thread_id": thread_id})
            delta = event.get("delta")
            if isinstance(delta, str) and delta:
                emit(
                    {
                        "type": "agent.message.delta",
                        "thread_id": thread_id,
                        "delta": delta,
                    }
                )
        elif kind == "CUSTOM" and event.get("name") == "challenge.progress":
            value = event.get("value") or {}
            if isinstance(value, dict):
                emit(
                    {
                        "type": "stage.updated",
                        "thread_id": thread_id,
                        "stage": value.get("stage"),
                        "status": value.get("status"),
                        "detail": value.get("detail"),
                    }
                )
        elif kind in ("TOOL_CALL_START", "TOOL_CALL_END"):
            tool_call_id = event.get("toolCallId")
            tool_name = event.get("toolCallName")
            if (
                kind == "TOOL_CALL_START"
                and isinstance(tool_call_id, str)
                and isinstance(tool_name, str)
            ):
                tool_call_names[tool_call_id] = tool_name
            if not tool_name and isinstance(tool_call_id, str):
                tool_name = tool_call_names.get(tool_call_id)
            emit(
                {
                    "type": "tool.started"
                    if kind == "TOOL_CALL_START"
                    else "tool.finished",
                    "thread_id": thread_id,
                    "tool": tool_name,
                    "tool_call_id": tool_call_id,
                }
            )

    async def run_attempt(input: str, turns: list[RTTurn] | None = None) -> RTTurn:
        nonlocal attempt_number, winning_case
        attempt_number += 1
        thread_id = thread_id_for(run_id, input, turns)
        responding_threads.discard(thread_id)
        attempt_log = log.bind(attempt=attempt_number, thread_id=thread_id)
        attempt_started = perf_counter()
        if emit:
            emit(
                {
                    "type": "attempt.started",
                    "thread_id": thread_id,
                    "attempt": attempt_number,
                    "prompt": input,
                }
            )
        attempt_log.info(
            "attempt_started",
            prompt_preview=input[:2000],
            prompt_chars=len(input),
            prior_turns=len(turns or []),
        )
        try:
            call_options = (
                {"on_event": lambda event: relay_agent_event(thread_id, event)}
                if emit
                else {}
            )
            result = await wait_for_agent_or_stop(
                call_agui(
                    str(request.target_url),
                    headers,
                    state,
                    input,
                    turns=turns,
                    thread_id=thread_id,
                    **call_options,
                )
            )
        except Exception as exc:
            abort_work()
            attempt_log.error(
                "attempt_failed",
                duration_ms=round((perf_counter() - attempt_started) * 1000),
                error_type=type(exc).__name__,
                error=str(exc)[:1000],
            )
            if emit:
                emit(
                    {
                        "type": "attempt.failed",
                        "thread_id": thread_id,
                        "attempt": attempt_number,
                        "detail": str(exc)[:300],
                    }
                )
            raise
        attempt_log.info(
            "attempt_completed",
            duration_ms=round((perf_counter() - attempt_started) * 1000),
            response_preview=result.text[:1000],
            response_chars=len(result.text),
            tools_called=result.tool_names,
            content_filtered=result.filtered,
        )
        if emit:
            emit(
                {
                    "type": "attempt.completed",
                    "thread_id": thread_id,
                    "attempt": attempt_number,
                    "tools": result.tool_names,
                    "response": result.text,
                }
            )
        secret = mailbox_for(
            settings.AGENTGATEWAY_API_KEY, user_id, thread_id, request.difficulty
        )
        if disclosed_mailbox(secret, input, result.text, turns):
            abort_work()
            duration_ms = round((perf_counter() - started) * 1000)
            if emit:
                emit(
                    {
                        "type": "attempt.scored",
                        "thread_id": thread_id,
                        "score": 0.0,
                        "outcome": "attack_succeeded",
                        "duration_ms": duration_ms,
                    }
                )
            winning_case = LevelCracked(
                thread_id,
                {
                    "input": input,
                    "actual_output": result.text,
                    "score": 0.0,
                    "reason": objective.disclosure_reason,
                    "error": None,
                    "vulnerability": objective.name,
                    "vulnerability_type": objective.attack_types[0],
                },
            )
            raise winning_case
        return RTTurn(
            role="assistant",
            content=result.text,
            tools_called=[ToolCall(name=name) for name in result.tool_names] or None,
        )

    async def model_callback(input: str, turns: list[RTTurn] | None = None) -> RTTurn:
        async with attempt_lock:
            if winning_case is not None:
                raise winning_case
            if stop_event.is_set():
                raise RuntimeError("DeepTeam run stopped after an internal error")
            return await run_attempt(input, turns)

    stopped_early = False
    try:
        if emit:
            emit(
                {
                    "type": "run.phase",
                    "phase": "attacker",
                    "detail": "Qwen is preparing attacks",
                }
            )
        attacker = LocalQwenModel(
            request.attacker, settings.OLLAMA_BASE_URL, stop_event=stop_event
        )
        vulnerability = MailboxDisclosure(
            name=objective.name,
            criteria=objective.criteria,
            types=objective.attack_types,
            custom_prompt=objective.generation_prompt,
            simulator_model=attacker,
            metric_options={
                "key": settings.AGENTGATEWAY_API_KEY,
                "user_id": user_id,
                "run_id": run_id,
                "difficulty": request.difficulty,
                "disclosure_reason": objective.disclosure_reason,
                "safe_reason": objective.safe_reason,
                "stop_on_success": True,
                **({"on_score": emit} if emit else {}),
            },
        )
        assessment = red_team(
            model_callback=model_callback,
            simulator_model=attacker,
            evaluation_model=attacker,
            target_purpose=f"{purpose} {objective.attacker_knowledge}",
            vulnerabilities=[vulnerability],
            attacks=build_attacks(request, attacker),
            attacks_per_vulnerability_type=request.run.attacks_per_type,
            run_all_attacks=request.run.run_all_attacks,
            max_concurrent=request.run.max_concurrent,
            ignore_errors=False,
            async_mode=True,
        )
        result = assessment.model_dump(mode="json")
    except LevelCracked as cracked:
        abort_work()
        cancel_pending_tasks()
        stopped_early = True
        result = {"test_cases": [cracked.case]}
    except asyncio.CancelledError as exc:
        abort_work()
        cancel_pending_tasks()
        if winning_case is not None:
            stopped_early = True
            result = {"test_cases": [winning_case.case]}
        else:
            log.error("run_cancelled", attempts=attempt_number)
            if emit:
                emit({"type": "run.failed", "detail": "The evaluation was cancelled."})
            raise RuntimeError("DeepTeam run cancelled") from exc
    except Exception as exc:
        abort_work()
        cancel_pending_tasks()
        log.exception(
            "run_failed",
            duration_ms=round((perf_counter() - started) * 1000),
            attempts=attempt_number,
        )
        if emit:
            emit({"type": "run.failed", "detail": str(exc)[:300]})
        raise

    outcomes = {
        "defense_passed": 0,
        "attack_succeeded": 0,
        "unscored": 0,
        "error": 0,
    }
    for case in result.get("test_cases", []):
        outcome = (
            "error"
            if case.get("error")
            else "unscored"
            if case.get("score") is None
            else "defense_passed"
            if case["score"] > 0
            else "attack_succeeded"
        )
        outcomes[outcome] += 1
        log.info(
            "test_case_scored",
            vulnerability=case.get("vulnerability"),
            vulnerability_type=case.get("vulnerability_type"),
            attack_method=case.get("attack_method"),
            prompt_preview=(case.get("input") or "")[:2000],
            outcome=outcome,
            score=case.get("score"),
            reason_preview=(case.get("reason") or "")[:1000],
            error=(case.get("error") or "")[:1000],
        )
    log.info(
        "run_completed",
        duration_ms=round((perf_counter() - started) * 1000),
        attempts=attempt_number,
        test_cases=len(result.get("test_cases", [])),
        **outcomes,
    )
    duration_ms = round((perf_counter() - started) * 1000)
    response = {
        "run_id": run_id,
        "user_id": user_id,
        "assessment": result,
        "outcomes": outcomes,
        "stopped_early": stopped_early,
        "duration_ms": duration_ms,
    }
    if emit:
        emit(
            {
                "type": "run.completed",
                "run_id": run_id,
                "outcomes": outcomes,
                "stopped_early": stopped_early,
                "duration_ms": duration_ms,
            }
        )
    return response
