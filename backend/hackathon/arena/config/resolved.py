"""A level resolved for one difficulty: files loaded, load-time placeholders rendered, references validated.

This is what the engine, the graph builder and the API consume. It is a plain pydantic model so a run can store
it as a snapshot (`model_dump(mode="json")`).
"""

from typing import Any, Literal

from pydantic import BaseModel

from hackathon.arena.config.schema import (
    AttackSurface,
    Limits,
    ModelSpec,
    RedTeamConfig,
    SecretConfig,
    SuccessConfig,
)
from hackathon.arena.registry.secrets import SECRET_KINDS, SecretContext
from hackathon.arena.registry.success import SUCCESS_KINDS, Observation, SuccessResult
from hackathon.arena.registry.tools import (
    TOOL_KINDS,
    BoundTool,
    ToolState,
    build_toolset,
)


class InjectionError(ValueError):
    """Content for the injection point does not match the configured fields."""


class PromptFile(BaseModel):
    """A prompt file: `text` has load-time placeholders rendered and keeps runtime ones (`runtime`)."""

    file: str  # path relative to backend/levels/
    text: str
    runtime: list[str] = []


class ToolInfo(BaseModel):
    name: str
    description: str
    sequential: bool
    parameters: list[str]
    toolset: str
    kind: str


class ResolvedToolset(BaseModel):
    id: str
    kind: str
    data_file: str | None
    data: Any
    params: dict[str, Any]
    descriptions: dict[str, str]
    tools: list[ToolInfo]


class ResolvedAgent(BaseModel):
    name: str
    model: ModelSpec
    system_prompt: str  # final text sent to the model
    system_prompt_file: PromptFile  # template (base) file
    security_prompt: PromptFile | None
    toolsets: list[ResolvedToolset]


class ResolvedGuardrail(BaseModel):
    id: str
    kind: str
    at: str
    tool: str | None  # wrapped tool for around:<tool>
    order: int  # index within its position
    name: str
    description: str
    model: ModelSpec | None
    system_prompt: PromptFile | None
    policy: PromptFile | None
    on_block: Literal["refuse", "replace_output", "tool_error"] | None
    on_error: Literal["block", "allow"]
    timeout_seconds: float
    blocked_message: str | None


class ResolvedGreenTeam(BaseModel):
    generator: ModelSpec
    cases: int
    guidance: PromptFile


class ResolvedLevel(BaseModel):
    id: str
    title: str
    description: str
    order: int
    difficulty: str
    difficulties: list[str]
    variables: dict[str, str]
    surface: AttackSurface
    secret: SecretConfig
    agent: ResolvedAgent
    guardrails: list[ResolvedGuardrail]
    success: SuccessConfig
    limits: Limits
    refusal: PromptFile
    red_team: RedTeamConfig
    green_team: ResolvedGreenTeam
    # Raw content of level.yaml and every referenced file, keyed by path relative to backend/levels/.
    sources: dict[str, str]

    # --- lookups ---------------------------------------------------------------------------------------------

    @property
    def tools(self) -> list[ToolInfo]:
        return [tool for toolset in self.agent.toolsets for tool in toolset.tools]

    def tool(self, name: str) -> ToolInfo | None:
        return next((tool for tool in self.tools if tool.name == name), None)

    def guardrails_at(self, at: str) -> list[ResolvedGuardrail]:
        """Guardrails at one position (`user->agent`, `around:send_email`, …) in execution order."""
        return [guardrail for guardrail in self.guardrails if guardrail.at == at]

    def positions(self) -> list[str]:
        """Occupied positions, in first-use order."""
        return list(dict.fromkeys(guardrail.at for guardrail in self.guardrails))

    # --- deterministic behavior ----------------------------------------------------------------------------

    def generate_secret(self, context: SecretContext) -> str:
        return SECRET_KINDS[self.secret.kind].generate(self.secret.params, context)

    def injection_content(self, content: dict[str, Any]) -> dict[str, str]:
        """Validate content for the injection point; returns it ordered by field."""
        fields = self.surface.injection.fields
        names = [field.name for field in fields]
        if unknown := sorted(set(content) - set(names)):
            raise InjectionError(f"unknown injection field(s): {', '.join(unknown)}")
        result = {}
        for field in fields:
            value = content.get(field.name)
            if not isinstance(value, str):
                raise InjectionError(f"injection field '{field.name}' must be a string")
            if len(value) > field.max_length:
                raise InjectionError(
                    f"injection field '{field.name}' exceeds {field.max_length} characters"
                )
            result[field.name] = value
        return result

    def build_tools(
        self, state: ToolState, injection: dict[str, str] | None = None
    ) -> list[BoundTool]:
        """The agent's tools for one case, in configured order."""
        target = self.surface.injection.tool
        tools = []
        for toolset in self.agent.toolsets:
            owns_injection = any(tool.name == target for tool in toolset.tools)
            tools += build_toolset(
                TOOL_KINDS[toolset.kind],
                toolset_id=toolset.id,
                data=toolset.data,
                params=toolset.params,
                descriptions=toolset.descriptions,
                state=state,
                injection=injection if owns_injection else None,
            )
        return tools

    def check_success(self, observation: Observation) -> SuccessResult:
        return SUCCESS_KINDS[self.success.kind].check(self.success.params, observation)

    # --- LLM roles -----------------------------------------------------------------------------------------

    def model_roles(self) -> list[dict[str, Any]]:
        """Every LLM role with its model spec and the graph node that shows it (R-CFG-7, R-UI-6)."""
        roles = [{"role": "agent", "node": "agent", **self.agent.model.summary()}]
        roles += [
            {
                "role": f"guardrail:{guardrail.id}",
                "node": f"guardrail:{guardrail.id}",
                **guardrail.model.summary(),
            }
            for guardrail in self.guardrails
            if guardrail.model is not None
        ]
        red = self.red_team
        roles += [
            {"role": "red_team.attacker", "node": "attacker", **red.attacker.summary()},
            {
                "role": "red_team.simulator",
                "node": "attacker",
                **red.simulator.summary(),
            },
            {
                "role": "red_team.evaluator",
                "node": "attacker",
                **red.evaluator.summary(),
            },
            {
                "role": "green_team.generator",
                "node": None,
                **self.green_team.generator.summary(),
            },
        ]
        return roles
