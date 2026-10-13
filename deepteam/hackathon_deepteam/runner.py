"""DeepTeam scenarios and models. The AG-UI adapter lives in agui_target.py."""

from __future__ import annotations

from time import perf_counter
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import structlog
from deepeval.models import LocalModel
from deepteam import red_team
from deepteam.test_case import RTTurn, ToolCall
from deepteam.vulnerabilities import Bias, PromptLeakage, RBAC

from hackathon_deepteam.agui_target import call_agui, thread_id_for
from hackathon_deepteam.config import Settings
from hackathon_deepteam.utils import NoRefinementAttackEngine

if TYPE_CHECKING:
    from hackathon_deepteam.api.runs import RunRequest

logger = structlog.get_logger("deepteam")


def run_deepteam(
    request: RunRequest, settings: Settings, headers: dict[str, str], user_id: str
) -> dict[str, Any]:
    run_id = str(uuid4())
    log = logger.bind(run_id=run_id, user_id=user_id)
    started = perf_counter()
    log.info(
        "run_started",
        goal=request.goal,
        target_host=request.target_url.host,
        target_path=request.target_url.path,
        model=request.model,
        vulnerabilities=request.vulnerabilities,
        attacks_per_vulnerability_type=request.attacks_per_vulnerability_type,
    )
    state = request.target_state
    if state is None:
        state = {"settings": {"modelId": request.model, "mode": "PLAYGROUND"}}
    {
        "bias": lambda: Bias(types=["race"]),
        "prompt_leakage": PromptLeakage,
        "rbac": RBAC,
    }
    attempt_number = 0

    async def model_callback(input: str, turns: list[RTTurn] | None = None) -> RTTurn:
        nonlocal attempt_number
        attempt_number += 1
        thread_id = thread_id_for(run_id, input, turns)
        attempt_log = log.bind(
            attempt=attempt_number,
            thread_id=thread_id,
        )
        attempt_started = perf_counter()
        attempt_log.info(
            "attempt_started",
            prompt_preview=input[:2000],
            prompt_chars=len(input),
            prior_turns=len(turns or []),
        )
        try:
            result = await call_agui(
                str(request.target_url),
                headers,
                state,
                input,
                turns=turns,
                thread_id=thread_id,
            )
        except Exception as exc:
            attempt_log.error(
                "attempt_failed",
                duration_ms=round((perf_counter() - attempt_started) * 1000),
                error_type=type(exc).__name__,
                error=str(exc)[:1000],
            )
            raise
        attempt_log.info(
            "attempt_completed",
            duration_ms=round((perf_counter() - attempt_started) * 1000),
            response_preview=result.text[:1000],
            response_chars=len(result.text),
            tools_called=result.tool_names,
        )
        return RTTurn(
            role="assistant",
            content=result.text,
            tools_called=[ToolCall(name=name) for name in result.tool_names] or None,
        )

    try:
        model = LocalModel(
            model=request.model,
            base_url=settings.AGENTGATEWAY_BASE_URL,
            api_key=settings.AGENTGATEWAY_API_KEY,
            generation_kwargs={"response_format": {"type": "json_object"}},
        )

        assessment = red_team(
            model_callback=model_callback,
            simulator_model=model,
            evaluation_model=model,
            target_purpose=request.goal,
            vulnerabilities=[
                RBAC(types=["role_bypass"]),
                PromptLeakage(types=["instructions"]),
            ],
            attacks=[],
            attack_engine=NoRefinementAttackEngine(
                simulator_model=model,
            ),
            attacks_per_vulnerability_type=2,
            max_concurrent=2,
            ignore_errors=False,
            async_mode=True,
        )
        result = assessment.model_dump(mode="json")
    except Exception:
        log.exception(
            "run_failed",
            duration_ms=round((perf_counter() - started) * 1000),
            attempts=attempt_number,
        )
        raise

    outcomes = {"defense_passed": 0, "attack_succeeded": 0, "error": 0}
    for case in result.get("test_cases", []):
        outcome = (
            "error"
            if case.get("error")
            else "defense_passed"
            if case.get("score") is not None and case["score"] > 0
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
    return {"run_id": run_id, "user_id": user_id, "assessment": result}
