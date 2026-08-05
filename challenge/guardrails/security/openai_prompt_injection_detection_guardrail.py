from typing import override

from guardrails import GuardrailTripwireTriggered
from guardrails.runtime import check_plain_text as openai_prompt_injection_detection
import structlog
from structlog import BoundLogger

from challenge.guardrails.security.basic_llm_judge_guardrail import BasicLLMJudgeGuardrailMiddleware

logger: BoundLogger = structlog.get_logger("open_ai_safety_guardrail")

# Can be created here: https://guardrails.openai.com/
# Anything except guardrails needs to be removed, because we only use check_plain_text (openai_prompt_injection_detection)
GUARDRAILS_CONFIG = {
    "guardrails": [
        {
            "name": "Prompt Injection Detection",
            "config": {
                "confidence_threshold": 0.7,
                "model": "gpt-4.1-mini"
            }
        }
    ]
}


class OpenAIPromptInjectionDetectionGuardrailMiddleware(BasicLLMJudgeGuardrailMiddleware):
    """
    Model-based guardrail:
    Use OpenAI Prompt Injection Detection to evaluate input/response safety.
    Sources:
      - https://openai.github.io/openai-guardrails-python/ref/checks/prompt_injection_detection/
      - https://openai.github.io/openai-guardrails-python/ref/runtime/#guardrails.runtime.check_plain_text
    Args:
        after_model: Whether to apply the guardrail on the model output.
        wrap_tool_call: Whether to apply the guardrail when wrapping tool calls.
        before_model: Whether to apply the guardrail before the model.
        allow_failure: Whether to allow the guardrail to fail.
        continue_graph_execution: Whether to continue the agent execution if the guardrail fails. If False, jumps to end.
    """

    def __init__(self, after_model: bool = False, wrap_tool_call: bool = False, before_model: bool = False,
                 allow_failure: bool = False, continue_graph_execution: bool = True):
        super().__init__()
        if not after_model and not wrap_tool_call and not before_model:
            raise ValueError(
                "One of input parameters after_model, wrap_tool_call, before_model must be True. "
                "Otherwise, middleware has no effect"
            )

        self.before_model = before_model
        self.after_model = after_model
        self.wrap_tool_call = wrap_tool_call

        self.allow_failure = allow_failure
        self.continue_graph_execution = continue_graph_execution

    @override
    async def _output_is_unsafe(self, content: str) -> bool:
        try:
            await openai_prompt_injection_detection(
                text=content,
                bundle_path=GUARDRAILS_CONFIG
            )
            return False
        # Exception is triggered when content is unsafe
        except GuardrailTripwireTriggered as e:
            logger.debug(f"Content flagged as unsafe: {content[:100]}...")
            return True
