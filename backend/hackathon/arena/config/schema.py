"""Pydantic schema of a level file (`backend/levels/<id>/level.yaml`) after difficulty resolution.

A level file holds a base and `difficulties`; each difficulty is deep-merged over the base (lists are replaced)
and the merged document is validated with `LevelVariant`. File references (prompts, data) are paths relative to
the level directory, falling back to the levels root (e.g. `shared/prompts/refusal.md`). Field names are binding
(docs/spec/config-driven-arena/IMPLEMENTATION.md §2).
"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Slug = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_-]*$")]
Identifier = Annotated[str, StringConstraints(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModelParams(Strict):
    """Model parameters of one LLM role; unset values are not sent to the provider."""

    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, gt=0, le=1)
    frequency_penalty: float | None = Field(default=None, ge=-2, le=2)
    presence_penalty: float | None = Field(default=None, ge=-2, le=2)
    max_tokens: int | None = Field(default=None, gt=0)
    reasoning_effort: Literal["none", "minimal", "low", "medium", "high"] | None = None
    seed: int | None = None
    thinking: bool | None = None
    extra: dict[str, Any] = Field(default_factory=dict)

    def compact(self) -> dict[str, Any]:
        """The parameters that are set, for display and run records."""
        return self.model_dump(
            exclude_none=True, exclude={"extra"} if not self.extra else set()
        )


class ModelSpec(Strict):
    """Model and parameters of one LLM role (agent, judge, attacker, simulator, evaluator, generator)."""

    model: str = Field(min_length=1)
    endpoint: str = "agentgateway"
    params: ModelParams = Field(default_factory=ModelParams)

    def summary(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "endpoint": self.endpoint,
            "params": self.params.compact(),
        }


class InjectionField(Strict):
    name: Identifier
    max_length: int = Field(gt=0)
    description: str = ""


class Injection(Strict):
    point: Literal["user_message", "tool_output"]
    tool: str | None = None
    description: str = ""
    fields: list[InjectionField] = Field(default_factory=list)


class AttackSurface(Strict):
    channel: Literal["direct", "indirect"]
    turns: Literal["single", "multi"]
    max_turns: int = Field(default=1, ge=1)
    injection: Injection
    user_task: str | None = None

    @model_validator(mode="after")
    def check_shape(self) -> "AttackSurface":
        injection = self.injection
        if self.channel == "direct":
            if injection.point != "user_message":
                raise ValueError("a direct surface injects at point 'user_message'")
            if injection.tool or injection.fields:
                raise ValueError("a direct surface takes no injection tool or fields")
            if self.user_task is not None:
                raise ValueError("a direct surface has no fixed user_task")
        else:
            if injection.point != "tool_output":
                raise ValueError("an indirect surface injects at point 'tool_output'")
            if not injection.tool:
                raise ValueError("an indirect surface needs injection.tool")
            if not injection.fields:
                raise ValueError(
                    "an indirect surface needs at least one injection field"
                )
            if not self.user_task:
                raise ValueError("an indirect surface needs a fixed user_task")
            if self.turns != "single":
                raise ValueError("an indirect surface is single-turn")
        names = [field.name for field in injection.fields]
        if len(names) != len(set(names)):
            raise ValueError("injection field names must be unique")
        if self.turns == "single" and self.max_turns != 1:
            raise ValueError("max_turns must be 1 for a single-turn surface")
        if self.turns == "multi" and self.max_turns < 2:
            raise ValueError("max_turns must be at least 2 for a multi-turn surface")
        return self


class SecretConfig(Strict):
    kind: str
    params: dict[str, Any] = Field(default_factory=dict)
    description: str = ""
    embedded_in: str = ""


class ToolsetConfig(Strict):
    """One tool kind with its data; a kind contributes one or more tools."""

    kind: str
    id: Slug | None = None
    data: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    descriptions: dict[str, str] = Field(default_factory=dict)


class AgentConfig(Strict):
    name: str = Field(min_length=1)
    model: ModelSpec
    system_prompt: str
    security_prompt: str | None = None
    tools: list[ToolsetConfig] = Field(default_factory=list)


class GuardrailConfig(Strict):
    id: Slug
    kind: str
    at: str
    name: str | None = None
    label: str | None = None
    description: str = ""
    model: ModelSpec | None = None
    system_prompt: str | None = None
    policy: str | None = None
    on_block: Literal["refuse", "replace_output", "tool_error"] | None = None
    on_error: Literal["block", "allow"] = "block"
    timeout_seconds: float = Field(default=30, gt=0)
    blocked_message: str | None = None


class SuccessConfig(Strict):
    kind: str
    params: dict[str, Any] = Field(default_factory=dict)
    description: str = ""


class LimitPair(Strict):
    model_requests: int = Field(gt=0)
    tool_calls: int = Field(gt=0)


class Limits(Strict):
    run: LimitPair
    conversation: LimitPair
    timeout_seconds: float = Field(gt=0)


class Objective(Strict):
    purpose: str = Field(min_length=1)
    criteria: str = Field(min_length=1)
    attacker_knowledge: str = Field(min_length=1)


class AttackConfig(Strict):
    type: str
    attempts: int = Field(ge=1)


class RedTeamConfig(Strict):
    attacker: ModelSpec
    simulator: ModelSpec
    evaluator: ModelSpec
    objective: Objective
    attacks: list[AttackConfig] = Field(min_length=1)


class GreenTeamConfig(Strict):
    generator: ModelSpec
    cases: int = Field(ge=1)
    guidance: str


class LevelVariant(Strict):
    """One difficulty of a level after deep-merging the variant over the base."""

    id: Slug
    title: str = Field(min_length=1)
    description: str = ""
    order: int = 100  # position in the level list
    variables: dict[str, str] = Field(default_factory=dict)
    attack_surface: AttackSurface
    secret: SecretConfig
    agent: AgentConfig
    guardrails: list[GuardrailConfig] = Field(default_factory=list)
    success: SuccessConfig
    limits: Limits
    refusal: str
    red_team: RedTeamConfig
    green_team: GreenTeamConfig
