"""Framework-neutral attack-type vocabulary (names only).

The deepteam service maps each name to a DeepTeam attack class; the backend only needs to know whether a type is
single- or multi-turn (a multi-turn type needs a multi-turn attack surface).
"""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class AttackType:
    name: str
    title: str
    turns: Literal["single", "multi"]


ATTACK_TYPES: dict[str, AttackType] = {
    attack.name: attack
    for attack in [
        AttackType("prompt_injection", "Prompt injection", "single"),
        AttackType("roleplay", "Roleplay", "single"),
        AttackType("leetspeak", "Leetspeak", "single"),
        AttackType("rot13", "ROT13", "single"),
        AttackType("base64", "Base64", "single"),
        AttackType("multilingual", "Multilingual", "single"),
        AttackType("math_problem", "Math problem", "single"),
        AttackType("gray_box", "Gray box", "single"),
        AttackType("prompt_probing", "Prompt probing", "single"),
        AttackType("system_override", "System override", "single"),
        AttackType("permission_escalation", "Permission escalation", "single"),
        AttackType("goal_redirection", "Goal redirection", "single"),
        AttackType("context_poisoning", "Context poisoning", "single"),
        AttackType("emotional_manipulation", "Emotional manipulation", "single"),
        AttackType("linear_jailbreaking", "Linear jailbreaking", "multi"),
        AttackType("tree_jailbreaking", "Tree jailbreaking", "multi"),
        AttackType("crescendo_jailbreaking", "Crescendo jailbreaking", "multi"),
        AttackType("sequential_jailbreak", "Sequential jailbreak", "multi"),
        AttackType("bad_likert_judge", "Bad Likert judge", "multi"),
    ]
}
