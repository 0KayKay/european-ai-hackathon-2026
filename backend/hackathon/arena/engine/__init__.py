"""Arena execution engine: run one case of a resolved level and record its events (IMPLEMENTATION.md §3)."""

from hackathon.arena.engine.events import CaseEvent, EventListener, EventRecorder
from hackathon.arena.engine.guardrails import ArenaGuardrails, LimitExceeded
from hackathon.arena.engine.models import (
    EngineModels,
    GatewayJudge,
    Judge,
    ModelIdentity,
    case_model_roles,
    gateway_agent_model,
)
from hackathon.arena.engine.session import (
    Case,
    CaseInput,
    CaseResult,
    CaseSession,
    CaseUsage,
    TurnResult,
    run_case,
)

__all__ = [
    "ArenaGuardrails",
    "Case",
    "CaseEvent",
    "CaseInput",
    "CaseResult",
    "CaseSession",
    "CaseUsage",
    "EngineModels",
    "EventListener",
    "EventRecorder",
    "GatewayJudge",
    "Judge",
    "LimitExceeded",
    "ModelIdentity",
    "TurnResult",
    "case_model_roles",
    "gateway_agent_model",
    "run_case",
]
