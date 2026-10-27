"""Guardrail kinds, positions and the deterministic parts of guardrail execution.

The engine (WP4) runs guardrails at their positions; this module provides what it needs that does not depend on a
live model: position rules, the sanitizer, the judge request (OpenAI chat-completions keyword arguments) and the
parsing of the judge's structured decision. Constants reproduce `hackathon.agent.guardrails`.
"""

import re
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field

from hackathon.agent.sanitization import sanitize_text
from hackathon.arena.config.schema import ModelSpec
from hackathon.arena.models import chat_completion_params

USER_TO_AGENT = "user->agent"
AGENT_TO_OUTPUT = "agent->output"
TOOL_TO_AGENT = "tool->agent"
AROUND = "around:"
FLOW_POSITIONS = (USER_TO_AGENT, AGENT_TO_OUTPUT, TOOL_TO_AGENT)

POSITION_TITLES = {
    USER_TO_AGENT: "Input guardrails",
    AGENT_TO_OUTPUT: "Output guardrails",
    TOOL_TO_AGENT: "Tool-result guardrails",
}

# The only block behavior that makes sense at each position.
ON_BLOCK = {
    USER_TO_AGENT: "refuse",
    AGENT_TO_OUTPUT: "replace_output",
    TOOL_TO_AGENT: "tool_error",
    AROUND: "tool_error",
}

SANITIZER_RULE = "Character normalization"
SANITIZER_REASON_CHANGED = "Obfuscated or invisible characters were normalized."
SANITIZER_REASON_UNCHANGED = "Text checked; no character changes needed."
JUDGE_PENDING_REASON = "Evaluating the security policy."
JUDGE_UNAVAILABLE_RULE = "Check unavailable"
JUDGE_UNAVAILABLE_REASON = (
    "The security check could not complete. The action was blocked."
)


def position_key(at: str) -> str:
    """`around:send_email` -> `around:`; flow positions map to themselves."""
    return AROUND if at.startswith(AROUND) else at


def position_title(at: str) -> str:
    if at.startswith(AROUND):
        return f"Guardrails around {at.removeprefix(AROUND)}"
    return POSITION_TITLES[at]


@dataclass(frozen=True)
class GuardrailKind:
    name: str
    title: str
    positions: frozenset[str]  # flow positions and/or AROUND
    uses_model: bool
    needs_blocked_message: bool = False
    policy_runtime: tuple[
        str, ...
    ] = ()  # runtime placeholders of the policy prompt (args.* added per tool)
    policy_tool_args: bool = False

    def allows(self, at: str) -> bool:
        return position_key(at) in self.positions


GUARDRAIL_KINDS: dict[str, GuardrailKind] = {
    kind.name: kind
    for kind in [
        GuardrailKind(
            name="sanitizer",
            title="Sanitizer",
            positions=frozenset(FLOW_POSITIONS),
            uses_model=False,
        ),
        GuardrailKind(
            name="llm_judge",
            title="LLM judge",
            positions=frozenset(FLOW_POSITIONS),
            uses_model=True,
            policy_runtime=("content",),
        ),
        GuardrailKind(
            name="tool_authorization",
            title="Tool authorization",
            positions=frozenset({AROUND}),
            uses_model=True,
            needs_blocked_message=True,
            policy_runtime=("user_context",),
            policy_tool_args=True,
        ),
    ]
}


# --- sanitizer -------------------------------------------------------------------------------------------------


def sanitize(value: Any) -> tuple[Any, bool]:
    """Normalize every string in `value`; return the result and whether anything changed. Never blocks."""

    def walk(item: Any) -> Any:
        if isinstance(item, str):
            return sanitize_text(item)
        if isinstance(item, list):
            return [walk(element) for element in item]
        if isinstance(item, dict):
            return {key: walk(element) for key, element in item.items()}
        return item

    result = walk(value)
    return result, result != value


def sanitizer_reason(changed: bool) -> str:
    return SANITIZER_REASON_CHANGED if changed else SANITIZER_REASON_UNCHANGED


# --- LLM judge ---------------------------------------------------------------------------------------------------


class GuardrailDecision(BaseModel):
    decision: Literal["SAFE", "UNSAFE"]
    rule: str = Field(max_length=120)
    reason: str = Field(max_length=350)


def judge_request(spec: ModelSpec, system_prompt: str, prompt: str) -> dict[str, Any]:
    """Keyword arguments for `AsyncOpenAI().chat.completions.create` of one judge call."""
    return {
        "model": spec.model,
        **chat_completion_params(spec),
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "guardrail_decision",
                "strict": True,
                "schema": {
                    **GuardrailDecision.model_json_schema(),
                    "additionalProperties": False,
                },
            },
        },
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
    }


def parse_judge_content(content: str | None) -> GuardrailDecision:
    """Parse the judge's reply; tolerates a Markdown code fence. Raises on invalid content (= block)."""
    text = (content or "").strip()
    text = re.sub(r"\A```(?:json)?\s*|\s*```\Z", "", text, flags=re.I)
    return GuardrailDecision.model_validate_json(text)
