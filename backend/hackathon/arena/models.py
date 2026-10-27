"""ModelSpec -> provider parameters per LLM role.

Only the pure parameter mapping lives here so far; building clients (agentgateway base URL, headers) is part of the
engine (WP4).
"""

from typing import Any

from hackathon.arena.config.schema import ModelSpec

_SAMPLING = ("temperature", "top_p", "frequency_penalty", "presence_penalty", "seed")


def agent_model_settings(spec: ModelSpec) -> dict[str, Any]:
    """pydantic-ai `OpenAIChatModelSettings` for the target agent."""
    params = spec.params
    settings: dict[str, Any] = {}
    if params.thinking is not None:
        settings["thinking"] = params.thinking
    if params.reasoning_effort is not None:
        settings["openai_reasoning_effort"] = params.reasoning_effort
    if params.max_tokens is not None:
        settings["max_tokens"] = params.max_tokens
    for name in _SAMPLING:
        if (value := getattr(params, name)) is not None:
            settings[name] = value
    if params.extra:
        settings["extra_body"] = dict(params.extra)
    return settings


def chat_completion_params(spec: ModelSpec) -> dict[str, Any]:
    """Keyword arguments for `chat.completions.create` (judges and other direct OpenAI-compatible calls)."""
    params = spec.params
    values: dict[str, Any] = {
        name: value
        for name in _SAMPLING
        if (value := getattr(params, name)) is not None
    }
    if params.reasoning_effort is not None:
        values["reasoning_effort"] = params.reasoning_effort
    if params.max_tokens is not None:
        values["max_completion_tokens"] = params.max_tokens
    if params.extra:
        values["extra_body"] = dict(params.extra)
    return values
