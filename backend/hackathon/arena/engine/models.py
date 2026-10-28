"""Model clients per LLM role of a case: the target agent's pydantic-ai model and the guardrail judges.

Both are built from the role's `ModelSpec` (R-CFG-7, R-EXE-7) and are injectable (`EngineModels`), so tests pass a
`FunctionModel` and a fake judge. Requests go to agentgateway with the same headers as the legacy implementation
(`hackathon.agent.builder`, `hackathon.agent.guardrails.judge`); headers and the API key are never recorded
(R-EXE-8).
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

import structlog
from openai import AsyncOpenAI
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIChatModelSettings
from pydantic_ai.providers.openai import OpenAIProvider

from hackathon.arena.config.resolved import ResolvedGuardrail, ResolvedLevel
from hackathon.arena.config.schema import ModelSpec
from hackathon.arena.models import agent_model_settings
from hackathon.arena.registry.guardrails import (
    GuardrailDecision,
    judge_request,
    parse_judge_content,
)
from hackathon.config import get_settings

GATEWAY = "agentgateway"


@dataclass(frozen=True)
class ModelIdentity:
    """Who a model request is made for; sent as gateway headers, never recorded."""

    user_id: str
    case_id: str
    correlation_id: str = ""


def correlation_id() -> str:
    return str(structlog.contextvars.get_contextvars().get("correlation_id", ""))


def endpoint_url(endpoint: str) -> str:
    """Base URL of a ModelSpec endpoint: `agentgateway` or an explicit http(s) URL."""
    if endpoint == GATEWAY:
        return get_settings().AGENTGATEWAY_URL
    if endpoint.startswith(("http://", "https://")):
        return endpoint
    raise ValueError(f"unknown model endpoint '{endpoint}'")


def endpoint_api_key(endpoint: str) -> str:
    """The gateway key goes only to agentgateway; level files are edited by participants, so an explicit URL
    must never receive it."""
    return get_settings().AGENTGATEWAY_API_KEY if endpoint == GATEWAY else "unused"


def gateway_headers(spec: ModelSpec, identity: ModelIdentity) -> dict[str, str]:
    return {
        "x-user-id": identity.user_id,
        "x-model": spec.model,
        "x-correlation-id": identity.correlation_id or correlation_id(),
    }


AgentModelFactory = Callable[[ModelSpec, ModelIdentity], Model]


def gateway_agent_model(spec: ModelSpec, identity: ModelIdentity) -> Model:
    """The target agent's model, like `hackathon.agent.builder.build_agent` but with the role's own params."""
    return OpenAIChatModel(
        spec.model,
        settings=OpenAIChatModelSettings(**agent_model_settings(spec)),
        provider=OpenAIProvider(
            openai_client=AsyncOpenAI(
                base_url=endpoint_url(spec.endpoint),
                api_key=endpoint_api_key(spec.endpoint),
                default_headers=gateway_headers(spec, identity),
            ),
        ),
    )


class Judge(Protocol):
    """Decides one LLM-judge or tool-authorization guardrail call; raising means the check failed."""

    def __call__(
        self, guardrail: ResolvedGuardrail, prompt: str
    ) -> Awaitable[GuardrailDecision]: ...


JudgeFactory = Callable[[ModelIdentity], Judge]


class GatewayJudge:
    """Judge over an OpenAI-compatible endpoint; same request as the legacy `judge()` for the legacy spec."""

    def __init__(
        self,
        identity: ModelIdentity,
        client_factory: Callable[..., Any] = AsyncOpenAI,
    ) -> None:
        self.identity = identity
        self.client_factory = client_factory

    async def __call__(
        self, guardrail: ResolvedGuardrail, prompt: str
    ) -> GuardrailDecision:
        spec = guardrail.model
        if spec is None or guardrail.system_prompt is None:
            raise ValueError(f"guardrail '{guardrail.id}' has no judge model")
        async with self.client_factory(
            base_url=endpoint_url(spec.endpoint),
            api_key=endpoint_api_key(spec.endpoint),
            timeout=guardrail.timeout_seconds,
            max_retries=0,
            default_headers={
                **gateway_headers(spec, self.identity),
                "x-thread-id": self.identity.case_id,
            },
        ) as client:
            response = await client.chat.completions.create(
                **judge_request(spec, guardrail.system_prompt.text, prompt)
            )
        return parse_judge_content(response.choices[0].message.content)


@dataclass(frozen=True)
class EngineModels:
    """Model clients for the LLM roles of a case (target agent, guardrail judges)."""

    agent: AgentModelFactory = gateway_agent_model
    judge: JudgeFactory = GatewayJudge


def case_model_roles(level: ResolvedLevel) -> list[dict[str, Any]]:
    """The LLM roles a case uses (agent and model guardrails) with the model and params applied (R-EXE-7)."""
    return [
        role
        for role in level.model_roles()
        if role["role"] == "agent" or role["role"].startswith("guardrail:")
    ]
