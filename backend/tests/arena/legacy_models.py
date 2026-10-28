"""The legacy implementation's model settings and changed prompts, for parity tests.

The level files default every LLM role to an open-weight model, while the legacy levels call Gemini, and the
offer-review system prompt was corrected on purpose (it named tools and arguments that do not exist). Parity (AC-1)
is about the rest of the level behaviour, so the parity tests put the legacy settings and prompt back.
"""

from copy import deepcopy

import yaml

from hackathon.agent.challenge_3.prompts import challenge3_prompt
from hackathon.arena.config.loader import LEVEL_FILE, LEVELS_ROOT, load_level_document

LEGACY_AGENT = {
    "model": "gemini-3.8-flash",
    "params": {"thinking": False, "reasoning_effort": "low", "max_tokens": 65536},
}
LEGACY_JUDGE = {
    "model": "gemini-3.8-flash",
    "params": {"temperature": 0, "reasoning_effort": "low", "max_tokens": 2048},
}


def use_legacy_models(document: dict) -> dict:
    document["agent"]["model"] = deepcopy(LEGACY_AGENT)
    variants = document.get("difficulties", {}).values()
    for guardrails in [
        document.get("guardrails"),
        *(v.get("guardrails") for v in variants),
    ]:
        for guardrail in guardrails or []:
            if "model" in guardrail:
                guardrail["model"] = deepcopy(LEGACY_JUDGE)
    return document


def legacy_model_levels() -> dict:
    levels = {}
    for path in sorted(LEVELS_ROOT.glob(f"*/{LEVEL_FILE}")):
        document = use_legacy_models(yaml.safe_load(path.read_text()))
        levels[path.parent.name] = load_level_document(document, path.parent)
    return levels


LEGACY_SYSTEM_PROMPTS = {"offer-review": challenge3_prompt}


def with_legacy_prompt(level_id: str, difficulty: str, resolved):
    """The resolved level with the legacy system prompt where the level file deliberately changed it."""
    if level_id not in LEGACY_SYSTEM_PROMPTS:
        return resolved
    prompt = LEGACY_SYSTEM_PROMPTS[level_id]("User", difficulty.upper())
    return resolved.model_copy(
        update={"agent": resolved.agent.model_copy(update={"system_prompt": prompt})}
    )
