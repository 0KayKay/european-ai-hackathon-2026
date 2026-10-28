"""Run configurable agent guards and register trusted tool entrypoints."""

from __future__ import annotations

import importlib
import inspect
import json
from dataclasses import dataclass
from functools import wraps
from typing import Any, Callable

from openai import AsyncOpenAI

from level_builder import LevelConfig
from hackathon.config import get_settings


@dataclass(frozen=True)
class GuardrailContext:
    level_id: str
    hook: str
    value: Any
    user_id: str
    tool_name: str | None = None
    settings: dict | None = None


@dataclass(frozen=True)
class GuardrailResult:
    value: Any
    blocked: bool = False
    reason: str = ""


def load_entrypoint(path: str, prefix: str = "hackathon.level_plugins.") -> Callable:
    module_name, separator, function_name = path.partition(":")
    if not separator or not module_name.startswith(prefix) or not function_name.isidentifier():
        raise ValueError(f"Invalid trusted entrypoint: {path}")
    function = getattr(importlib.import_module(module_name), function_name)
    if not callable(function):
        raise ValueError(f"Entrypoint is not callable: {path}")
    return function


async def run_guardrails(
    level: LevelConfig,
    hook: str,
    value: Any,
    deps: dict,
    tool_name: str | None = None,
) -> GuardrailResult:
    current = value
    for guardrail in level.guardrails:
        if guardrail.hook != hook or (guardrail.tool and guardrail.tool != tool_name):
            continue
        context = GuardrailContext(level.id, hook, current, deps["user_id"], tool_name, guardrail.settings)
        if guardrail.type == "custom":
            if not guardrail.entrypoint:
                raise ValueError(f"Custom guardrail {guardrail.id} has no entrypoint")
            result = load_entrypoint(guardrail.entrypoint)(context)
            if inspect.isawaitable(result):
                result = await result
            if not isinstance(result, GuardrailResult):
                raise TypeError(f"Custom guardrail {guardrail.id} must return GuardrailResult")
        else:
            if not guardrail.prompt:
                raise ValueError(f"LLM guardrail {guardrail.id} has no prompt")
            settings = get_settings()
            async with AsyncOpenAI(
                base_url=settings.AGENTGATEWAY_URL,
                api_key=settings.AGENTGATEWAY_API_KEY,
            ) as client:
                answer = await client.chat.completions.create(
                    model=guardrail.model,
                    temperature=0,
                    messages=[
                        {"role": "system", "content": guardrail.prompt},
                        {"role": "user", "content": current if isinstance(current, str) else json.dumps(current, ensure_ascii=False)},
                    ],
                )
            decision = (answer.choices[0].message.content or "").strip().upper()
            result = GuardrailResult(
                value=current,
                blocked=decision != "SAFE",
                reason=guardrail.blocked_message if decision != "SAFE" else "",
            )
        deps.setdefault("guardrail_events", []).append(
            {
                "stage": guardrail.id,
                "status": "error" if result.blocked else "success",
                "detail": result.reason,
            }
        )
        if result.blocked:
            return result
        current = result.value
    return GuardrailResult(value=current)


def register_tools(agent, level: LevelConfig) -> None:
    for tool in level.tools:
        function = load_entrypoint(tool.entrypoint)
        hooks = [guard for guard in level.guardrails if guard.hook in ("before_tool", "after_tool") and (not guard.tool or guard.tool == tool.name)]
        if not hooks:
            agent.tool(function, name=tool.name, description=tool.description)
            continue

        @wraps(function)
        async def guarded_tool(ctx, *args, _function=function, _tool=tool, **kwargs):
            bound = inspect.signature(_function).bind(ctx, *args, **kwargs)
            arguments = {name: value for name, value in bound.arguments.items() if name != "ctx"}
            before = await run_guardrails(level, "before_tool", arguments, ctx.deps, _tool.name)
            if before.blocked:
                return before.reason or "Tool call blocked by guardrail."
            updated = before.value
            if not isinstance(updated, dict):
                raise TypeError("A before_tool guardrail must return tool arguments as a dict")
            result = _function(ctx, **updated)
            if inspect.isawaitable(result):
                result = await result
            after = await run_guardrails(level, "after_tool", result, ctx.deps, _tool.name)
            return after.reason if after.blocked else after.value

        agent.tool(guarded_tool, name=tool.name, description=tool.description)
