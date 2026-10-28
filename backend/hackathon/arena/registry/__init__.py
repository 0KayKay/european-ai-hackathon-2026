"""Type registries of the arena: what a level config may name, and how each kind behaves."""

from hackathon.arena.registry.attacks import ATTACK_TYPES, AttackType
from hackathon.arena.registry.guardrails import GUARDRAIL_KINDS, GuardrailKind
from hackathon.arena.registry.secrets import SECRET_KINDS, SecretContext, SecretKind
from hackathon.arena.registry.success import (
    SUCCESS_KINDS,
    Observation,
    SuccessKind,
    SuccessResult,
)
from hackathon.arena.registry.tools import (
    TOOL_KINDS,
    BoundTool,
    ToolKind,
    ToolState,
    build_toolset,
)

__all__ = [
    "ATTACK_TYPES",
    "GUARDRAIL_KINDS",
    "SECRET_KINDS",
    "SUCCESS_KINDS",
    "TOOL_KINDS",
    "AttackType",
    "BoundTool",
    "GuardrailKind",
    "Observation",
    "SecretContext",
    "SecretKind",
    "SuccessKind",
    "SuccessResult",
    "ToolKind",
    "ToolState",
    "build_toolset",
]
