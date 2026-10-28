"""Example custom guardrail: block content matching a configured regular expression."""

import json
import re

from hackathon.agent.level_runtime import GuardrailContext, GuardrailResult


def block_if_matches(context: GuardrailContext) -> GuardrailResult:
    settings = context.settings or {}
    pattern = settings.get("pattern")
    if not isinstance(pattern, str) or not pattern:
        raise ValueError("regex_guard requires a nonempty settings.pattern")
    value = context.value
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    if re.search(pattern, text, re.IGNORECASE):
        return GuardrailResult(
            value=value,
            blocked=True,
            reason=str(settings.get("message", "Request blocked by regex guardrail.")),
        )
    return GuardrailResult(value=value)
