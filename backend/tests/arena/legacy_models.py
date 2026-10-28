"""The legacy implementation's model settings, for parity tests.

The level files default every LLM role to a Leonardo model, while the legacy levels call Gemini. Parity (AC-1)
is about level behaviour, so the parity tests load the levels with the legacy model settings put back.
"""

from copy import deepcopy

import yaml

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
    for guardrails in [document.get("guardrails"), *(v.get("guardrails") for v in variants)]:
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
