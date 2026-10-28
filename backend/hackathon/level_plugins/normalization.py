"""Example custom guardrail that normalizes Unicode before the input judge."""

import unicodedata

from hackathon.agent.level_runtime import GuardrailContext, GuardrailResult


def normalize_text(context: GuardrailContext) -> GuardrailResult:
    value = str(context.value)
    value = unicodedata.normalize("NFKC", value)
    value = value.replace("\u200b", "").replace("\u200c", "").replace("\u200d", "")
    return GuardrailResult(value=value)
