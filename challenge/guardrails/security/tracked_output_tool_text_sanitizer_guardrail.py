import structlog
from structlog import BoundLogger

from challenge.guardrails.security.tracked_text_sanitizer_guardrail import TrackedTextSanitizerGuardrailMiddleware

logger: BoundLogger = structlog.get_logger("text_sanitizer_guardrail")


class TrackedOutputToolTextSanitizerGuardrailMiddleware(TrackedTextSanitizerGuardrailMiddleware):
    """
    Text Sanitizer guardrail: Sanitize text input against all sorts of character-based attacks.
    See this paper: https://arxiv.org/html/2504.11168v1

    Args:
        after_model: Whether to apply the guardrail on the model output.
        wrap_tool_call: Whether to apply the guardrail when wrapping tool calls.
        before_model: Whether to apply the guardrail before the model.
        allow_error: Whether to allow the guardrail to fail.
    """
    guardrail_name = "OutputToolTextSanitizerGuardrail"

    after_model_can_jump_to = ["end"]

    def __init__(self):
        super().__init__(wrap_tool_call=True, after_model=True)
