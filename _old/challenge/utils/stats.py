from typing import TypedDict

from langchain_core.messages import UsageMetadata


class TokenStats(TypedDict):
    """Dependencies available in the agent's RunContext"""

    total_input_tokens: int
    total_output_tokens: int


def count_total_tokens(token_usage_dict: dict[str, UsageMetadata]) -> TokenStats:
    """Count total input and output tokens from usage metadata across all invoked models.

    Args:
        token_usage_dict: Dictionary mapping model names to their usage metadata

    Returns:
        Dictionary with 'total_input_tokens' and 'total_output_tokens' keys
    """
    total_input_tokens = 0
    total_output_tokens = 0
    for model, usage_metadata in token_usage_dict.items():
        total_input_tokens += usage_metadata.get("input_tokens", 0)
        total_output_tokens += usage_metadata.get("output_tokens", 0)

    return TokenStats(
        total_input_tokens=total_input_tokens, total_output_tokens=total_output_tokens
    )
