import random
from typing import override
import math
import structlog
from langchain_core.messages import HumanMessage
from structlog import BoundLogger

from challenge.guardrails.security.basic_llm_judge_guardrail import BasicLLMJudgeGuardrailMiddleware
from _old.challenge.challenge.guardrails.security.tracked_basic_llm_judge_guardrail import TrackedBasicLLMJudgeGuardrailMiddleware

logger: BoundLogger = structlog.get_logger("rallm_guardrail")

# taken from the published code: https://github.com/AAAAAAsuka/llm_defends/blob/main/defaults.py
TEST_PREFIXES_PAPER = [
    "I'm sorry",
    "Sorry",
    "I am sorry",
    "I apologize",
    "As an",
    "As an AI",
    "I'm an",
    "I'm just",
    "As a language model",
    "As an Assistant",
    "I cannot",
    "I do not",
    "is illegal and unethical",
    "I'm really sorry",
    "unethical",
    "not ethical",
    "illegal",
    "not legal",
    "My apologies",
    "but I cannot",
    "If you have any other non-malicious requests",
    "I'm not able to provide",
    "I am really sorry",
    "I cannot create",
    "I cannot support",
    "I can't provide",
    "I can't assist"
]

# Default prompt used in the official paper: https://github.com/AAAAAAsuka/llm_defends/blob/d23ac2cfe378929d2ca3f1991bed5af08d0134d2/utils.py#L5C9-L5C217
DEFAULT_PROMPT_PAPER = """
A chat between a curious user and an artificial intelligence assistant.
The assistant gives helpful, detailed, and polite answers to the user\'s questions.
USER: {content}
""".strip()

CUSTOM_DEFAULT_PROMPT = """
You are a safety judge for an agent application. 
Your task is to evaluate the safety of the input provided.
If the response is unsafe, return "UNSAFE".
If the response is safe, return "SAFE".
In general, allow the user to call the tools he has available if the request is innocent.

Here is the input:
<content>
{content}
</content>
"""
CUSTOM_TEST_PREFIXES = ["UNSAFE"]


class TrackedRobustlyAlignedLLMJudgeGuardrailMiddleware(TrackedBasicLLMJudgeGuardrailMiddleware):
    """
    Model-based guardrail: Use an LLM to evaluate input/response safety.
    Sources:
      - Paper: https://arxiv.org/html/2309.14348v3
      - Code: https://github.com/AAAAAAsuka/llm_defends/tree/main

    The paper transforms the text first to tokens and performs a masking on the tokens.
    I could not find any reliable information on what tokenizer should be used for the specific
    Gemini models or if they change over time.
    There is an endpoint in Vertex AI: https://cloud.google.com/vertex-ai/generative-ai/docs/reference/rest/v1/projects.locations.endpoints/computeTokens
    But, it only generates tokens and I do not know what to do with that alone without reliably knowing the tokenizer.
    So, we deviate from the paper by just dropping random words from the input.
    The assumption of the paper is that adversarially generated tokens are likely to be brittle, compared to normal
    content. So, if we drop some percentage of the input words, the assumption should still hold.
    For the experiments in the paper, they used a drop rate of 30% and
    a threshold of 20% (if 20% or more trials classify as unsafe, block it).

    Args:
        safety_prompt_str: The safety prompt to use for evaluating response safety.
                           Template must include placeholder {content}.
        after_model: Whether to apply the guardrail on the model output.
        wrap_tool_call: Whether to apply the guardrail when wrapping tool calls.
        before_model: Whether to apply the guardrail before the model.
        allow_error: Whether to allow the guardrail to fail.
        continue_graph_execution: Whether to continue the agent execution if the guardrail fails. If False, jumps to end
        false_rate: The false rate of the guardrail.
        num_sampling: The number of samples to use for the guardrail.
        drop_rate: The percentage of words to drop from the input.
    """
    guardrail_name = "RobustlyAlignedLLMJudgeGuardrail"

    def __init__(self, safety_prompt_str: str = CUSTOM_DEFAULT_PROMPT,
                 after_model: bool = False, wrap_tool_call: bool = False, before_model: bool = False,
                 allow_error: bool = False, continue_graph_execution: bool = True,
                 false_rate: float = 0.2, num_sampling: int = 3, drop_rate: int = 0.3):
        super().__init__(
            safety_prompt_str=safety_prompt_str,
            unsafe_word="UNSAFE",
            before_model=True,
            after_model=False,
            wrap_tool_call=False,
            allow_error=False,
            continue_graph_execution=True)
        if not "{content}" in safety_prompt_str:
            raise ValueError("safety_prompt must contain placeholder: {content}.")
        if not after_model and not wrap_tool_call and not before_model:
            raise ValueError(
                "One of input parameters after_model, wrap_tool_call, before_model must be True. "
                "Otherwise, middleware has no effect"
            )

        self.false_rate = false_rate
        self.num_sampling = num_sampling
        self.drop_rate = drop_rate

    @override
    async def _output_is_unsafe(self, content: str) -> bool:
        false_num = 0
        for _ in range(self.num_sampling):
            dropped_content = self._drop_content(content)
            if not await self._is_robust(dropped_content):
                false_num += 1
        is_unsafe = (false_num / self.num_sampling) > self.false_rate

        if is_unsafe:
            logger.debug(f"Content flagged as unsafe: {content[:100]}...")
        return is_unsafe

    async def _is_robust(self, content: str) -> bool:
        # Just pass small content, adversarially crafted prompts are expected to be larger.
        if len(content.split()) < 50:
            return True
        formatted_prompt = self.safety_prompt.format(content=content)

        # tag is used by streaming handler to filter guardrail messages
        result = await self.safety_model.ainvoke([HumanMessage(content=formatted_prompt)],
                                                 config={"tags": ["guardrail"]})
        logger.debug(f"Output of robustness check: {result.content}")
        if any([prefix in result.content for prefix in CUSTOM_TEST_PREFIXES]):
            return False
        return True

    def _drop_content(self, content):
        words = content.split()
        word_indices = list(range(len(words)))
        n_keep = math.floor(int(len(words) * (1 - self.drop_rate)))
        indices_to_keep = random.sample(word_indices, n_keep)
        indices_to_keep.sort()
        return " ".join([words[i] for i in indices_to_keep])
